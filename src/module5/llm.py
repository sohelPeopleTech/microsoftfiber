"""Azure AI Foundry / Azure OpenAI client -- thin, stdlib-only, optional.

Kept deliberately small because of where it sits: everything numeric in Module
5 is computed in pandas and already correct before this file is touched. The
model's only job is wording. If it is not configured, or errors, or returns
something that fails the grounding check, the caller falls back to the
deterministic text and the run still succeeds.

Two endpoint shapes are supported, detected from the endpoint URL, because the
Foundry v1 surface and the classic Azure OpenAI surface disagree on where the
model name goes:

  Foundry v1   https://<resource>.services.ai.azure.com/openai/v1
               POST {endpoint}/chat/completions, model in the body,
               Authorization: Bearer. No api-version. Serves non-OpenAI models
               (DeepSeek, Llama, Mistral) through the same OpenAI-shaped API.

  Azure OpenAI https://<resource>.openai.azure.com
               POST {endpoint}/openai/deployments/{deployment}/chat/completions
               ?api-version=..., model implied by the deployment, api-key header.

Configuration comes from the environment -- no credential is read from, or
written to, a tracked file in this repo:

    AZURE_FOUNDRY_ENDPOINT     or  AZURE_OPENAI_ENDPOINT
    AZURE_FOUNDRY_DEPLOYMENT   or  AZURE_OPENAI_DEPLOYMENT
    AZURE_FOUNDRY_API_KEY      or  AZURE_OPENAI_API_KEY
    AZURE_OPENAI_API_VERSION   (classic endpoints only; ignored on v1)
    AZURE_OPENAI_AD_TOKEN      Entra ID bearer token; preferred inside Fabric,
                               where a managed identity avoids a stored key

urllib rather than the openai SDK so this runs unchanged on a Fabric Spark pool
without a per-session pip install.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .transport import ssl_context

DEFAULT_API_VERSION = "2024-10-21"

#: Marker for the OpenAI-compatible v1 surface served by Azure AI Foundry.
_V1_PATH = "/openai/v1"


class LLMUnavailable(RuntimeError):
    """Not configured, unreachable, or refused. Always caught by callers."""


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""


@dataclass
class LLMConfig:
    endpoint: str = ""
    deployment: str = ""
    api_version: str = DEFAULT_API_VERSION
    api_key: str = ""
    ad_token: str = ""
    temperature: float = 0.2  # wording, not invention
    max_tokens: int = 700
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            endpoint=_first_env(
                "AZURE_FOUNDRY_ENDPOINT", "AZURE_OPENAI_ENDPOINT"
            ).rstrip("/"),
            deployment=_first_env("AZURE_FOUNDRY_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION),
            api_key=_first_env("AZURE_FOUNDRY_API_KEY", "AZURE_OPENAI_API_KEY"),
            ad_token=_first_env("AZURE_FOUNDRY_AD_TOKEN", "AZURE_OPENAI_AD_TOKEN"),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.deployment and (self.api_key or self.ad_token))

    @property
    def is_v1_endpoint(self) -> bool:
        return _V1_PATH in self.endpoint

    @property
    def url(self) -> str:
        if self.is_v1_endpoint:
            return f"{self.endpoint}/chat/completions"
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        # The v1 surface is bearer-only; the classic surface takes either, and
        # prefers the Entra token when both are present.
        if self.is_v1_endpoint:
            headers["Authorization"] = f"Bearer {self.ad_token or self.api_key}"
        elif self.ad_token:
            headers["Authorization"] = f"Bearer {self.ad_token}"
        else:
            headers["api-key"] = self.api_key
        return headers

    def body(self, system: str, user: str) -> dict:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.is_v1_endpoint:
            # On v1 the deployment is the model name, passed in the body.
            payload["model"] = self.deployment
        return payload

    def describe(self) -> str:
        """Safe to log -- endpoint and deployment only, never the credential."""
        if not self.is_configured:
            return "LLM: not configured (deterministic text only)"
        surface = "Foundry v1" if self.is_v1_endpoint else "Azure OpenAI"
        auth = "Entra ID token" if self.ad_token else "API key"
        return f"LLM: {self.deployment} via {surface} at {self.endpoint} ({auth})"


#: Back-compat alias -- the class covered only Azure OpenAI before Foundry.
AzureOpenAIConfig = LLMConfig


def chat(
    system: str,
    user: str,
    config: LLMConfig | None = None,
) -> str:
    """One completion. Raises LLMUnavailable on any failure -- never returns junk."""
    config = config or LLMConfig.from_env()
    if not config.is_configured:
        raise LLMUnavailable(
            "LLM not configured; set AZURE_FOUNDRY_ENDPOINT, "
            "AZURE_FOUNDRY_DEPLOYMENT and AZURE_FOUNDRY_API_KEY."
        )

    request = urllib.request.Request(
        config.url,
        data=json.dumps(config.body(system, user)).encode(),
        headers=config.headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=config.timeout, context=ssl_context()
        ) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise LLMUnavailable(f"LLM HTTP {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMUnavailable(f"LLM call failed: {exc}") from exc

    try:
        choice = body["choices"][0]
    except (KeyError, IndexError) as exc:
        raise LLMUnavailable(f"Unexpected LLM response: {body}") from exc

    message = choice.get("message") or {}
    text = (message.get("content") or "").strip()

    # Reasoning models return the visible answer in `content` and the chain of
    # thought separately; an empty `content` with reasoning present means the
    # model spent its budget thinking. Treat it as unavailable rather than
    # posting an empty finding, and say which case it was.
    if not text:
        finish = choice.get("finish_reason", "unknown")
        if message.get("reasoning_content"):
            raise LLMUnavailable(
                f"Model returned reasoning but no answer (finish_reason={finish}); "
                f"raise max_tokens above {config.max_tokens}."
            )
        raise LLMUnavailable(f"Empty completion (finish_reason={finish}).")
    return strip_reasoning(text)


def strip_reasoning(text: str) -> str:
    """Drop a <think>...</think> preamble.

    DeepSeek-family models emit their chain of thought inline. It must never
    reach a leadership channel, and it would fail the grounding check anyway --
    thinking out loud about figures is exactly where invented ones appear.
    """
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()
