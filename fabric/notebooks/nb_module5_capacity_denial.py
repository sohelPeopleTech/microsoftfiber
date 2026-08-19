# Fabric notebook source
#
# Module 5 -- Capacity-Denial Revenue Impact Calculator
#
# Runs the same code as the local CLI. The medallion functions are unchanged;
# only the read/write endpoints differ (Lakehouse Delta tables instead of
# files), so a number computed here matches the number computed on a laptop.
#
# Attach this notebook to the Fabric_Capacity_Intelligence Lakehouse in the
# Fabric Accelerators workspace before running. Parameters below are overridden
# by the Data Factory pipeline at run time -- see
# fabric/pipelines/module5_daily.json.

# PARAMETERS CELL ********************

# Target environment. The workspace ID is deliberately NOT hardcoded -- the
# notebook asks the runtime which workspace it is in, so this file stays
# portable across dev/prod workspaces and carries no environment identifiers.
# Only the pipeline needs the literal GUID, to say which notebook to run.
lakehouse_name = "Fabric_Capacity_Intelligence"

# Bronze source. Either a Files/ path in the attached Lakehouse, or a Delta
# table name already landed by an upstream ICM export.
ticket_source = "Files/module5/Synthetic_ICM_Capacity_Data.xlsx"
subscription_source = ""          # blank => same workbook as the tickets
expected_source = ""              # blank => same workbook (the labelled sample)
config_path = "Files/module5/config.json"

lakehouse_table_prefix = "module5_"
use_llm = False                   # LLM wording pass (Azure AI Foundry)
key_vault_name = ""               # e.g. kv-fabric-capacity
llm_key_secret = "module5-foundry-api-key"

# CELL ********************

# Confirm where we are running before anything reads or writes. A notebook
# attached to the wrong Lakehouse still runs -- it just silently reads nothing
# and writes Gold tables somewhere nobody is looking, which is the failure mode
# worth spending five lines to make impossible.
context = notebookutils.runtime.context  # noqa: F821
print(f"Workspace : {context.get('currentWorkspaceName')} ({context.get('currentWorkspaceId')})")
print(f"Lakehouse : {context.get('defaultLakehouseName')}")

attached = context.get("defaultLakehouseName")
if not attached:
    raise RuntimeError(
        "No default Lakehouse attached. Attach "
        f"'{lakehouse_name}' to this notebook before running."
    )
if attached != lakehouse_name:
    raise RuntimeError(
        f"Attached to Lakehouse '{attached}' but this notebook expects "
        f"'{lakehouse_name}'. Re-attach, or update lakehouse_name if the "
        f"Lakehouse was renamed."
    )

# CELL ********************

# The package reaches the notebook one of two ways:
#   1. A custom Environment with the fabric_capacity_intelligence wheel attached
#      (preferred -- versioned, no per-run copy), or
#   2. the src/ tree uploaded to Files/module5/src (fallback below).
import sys

SRC_PATH = "/lakehouse/default/Files/module5/src"
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from module5 import agent, aggregate, ingest, narrative, pipeline, recommend, report  # noqa: E402
from module5.config import Config  # noqa: E402
from module5.llm import LLMConfig  # noqa: E402

print(f"module5 loaded from {pipeline.__file__}")

# CELL ********************

# Secrets come from Key Vault via the workspace identity -- never from a
# notebook cell, a parameter, or a Lakehouse file.
import os

if key_vault_name:
    vault_url = f"https://{key_vault_name}.vault.azure.net/"
    if use_llm:
        os.environ["AZURE_FOUNDRY_API_KEY"] = notebookutils.credentials.getSecret(  # noqa: F821
            vault_url, llm_key_secret
        )
        # Endpoint and deployment are not secrets; keep them with the config.
        os.environ.setdefault(
            "AZURE_FOUNDRY_ENDPOINT",
            "https://mayus-mioz5b6n-eastus2.services.ai.azure.com/openai/v1",
        )
        os.environ.setdefault("AZURE_FOUNDRY_DEPLOYMENT", "DeepSeek-V4-Flash")

# CELL ********************

# Resolve Lakehouse-relative paths to the local mount the pandas readers use.
def lakehouse_path(relative: str) -> str:
    if not relative:
        return ""
    if relative.startswith(("/", "abfss://")):
        return relative
    return f"/lakehouse/default/{relative}"


config = Config.load(lakehouse_path(config_path)) if config_path else Config()
print(f"Delay cut-off: {config.meaningful_delay_hours}h | top N: {config.top_n_regions}")

# CELL ********************

result = pipeline.run(
    ticket_source=lakehouse_path(ticket_source),
    subscription_source=lakehouse_path(subscription_source) or None,
    expected_source=lakehouse_path(expected_source) or None,
    config=config,
    out_dir="/lakehouse/default/Files/module5/out",
    use_llm=use_llm,
    llm_config=LLMConfig.from_env() if use_llm else None,
)

for line in result.finding["data_quality"]["summary_lines"]:
    print(line)
print("Artefacts written -- the web application reads them from Files/module5/out.")

# The classifier gate. Fail the notebook so the pipeline surfaces it instead of
# quietly publishing tables computed by a classifier that just got a known
# answer wrong.
if result.blocked:
    raise RuntimeError(result.blocked_reason)

# CELL ********************

# Gold tables. Overwrite by design: each run republishes the current view of a
# fixed extract rather than appending duplicates. Switch to a merge on
# (IncidentId, AsOf) once the ICM feed becomes incremental.
import pandas as pd

TABLES = {
    f"{lakehouse_table_prefix}tickets_classified": result.priced,
    f"{lakehouse_table_prefix}region_exposure": result.regions,
    f"{lakehouse_table_prefix}customer_exposure": pd.DataFrame(
        result.finding["customers"]
    ),
    f"{lakehouse_table_prefix}exposure_trend": pd.DataFrame(result.finding["trend"]),
    f"{lakehouse_table_prefix}recommendations": pd.DataFrame(
        [
            {**r.to_dict(), "evidence": " | ".join(r.evidence)}
            for r in result.recommendations
        ]
    ),
}

for name, frame in TABLES.items():
    if frame.empty:
        print(f"{name}: nothing to write")
        continue
    spark.createDataFrame(frame).write.mode("overwrite").option(  # noqa: F821
        "overwriteSchema", "true"
    ).saveAsTable(name)
    print(f"{name}: {len(frame)} row(s)")

# CELL ********************

# The written finding -- what a person actually reads.
print(result.markdown)

# CELL ********************

# Ask a follow-up right here, the same way someone would in the application.
# The router is deterministic, so this answer and the written finding agree.
for question in (
    "which region is worst?",
    "why is uksouth ranked lower?",
    "how is exposure calculated?",
):
    print(f"Q: {question}")
    print(f"A: {agent.answer(question, result, allow_llm=use_llm).text}\n")

# CELL ********************

# Hand the pipeline a compact status it can branch on.
mssparkutils.notebook.exit(  # noqa: F821
    {
        "status": result.finding["status"],
        "as_of": result.finding["as_of"],
        "tickets_flagged": result.finding["summary"]["tickets_flagged"],
        "revenue_exposure_usd": result.finding["summary"]["revenue_exposure_usd"],
        "top_region": result.regions.iloc[0]["Region"] if len(result.regions) else None,
        "delivered": result.delivery.delivered,
    }
)
