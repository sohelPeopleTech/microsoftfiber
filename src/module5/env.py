"""Minimal .env loader.

python-dotenv would do this, but it would also be the only dependency in the
project that exists purely for local convenience -- and a Fabric Spark pool
would have to install it to run a file that never exists in Fabric anyway
(there, secrets come from Key Vault).

Existing environment variables always win, so an explicitly exported value is
never silently overridden by a stale .env.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env", override: bool = False) -> list[str]:
    """Load KEY=VALUE lines. Returns the names loaded -- never the values."""
    path = Path(path)
    if not path.exists():
        return []

    loaded = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if not key or not value:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
