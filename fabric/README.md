# Deploying Module 5 into the Fabric Accelerators workspace

| | |
|---|---|
| Workspace | `f78efe69-fe5e-494b-8433-77d828725ad1` |
| Lakehouse | `Fabric_Capacity_Intelligence` |
| Notebook | `nb_module5_capacity_denial` — `4f3bb0a5-edd5-46d6-870f-ead14c38af0a` |
| Pipeline | `PL_Module5_CapacityDenialRevenueImpact` — `16a11c2b-a5a0-4099-ad72-25bc33c23af5` |
| Key Vault | `kv-fabcap-ptg` (Rg-IPTool-Dev, eastus) |
| Status | **Deployed and verified 2026-08-11.** Weekly Mon 08:00 UTC, LLM on. |

> Everything below was deployed via the Fabric REST API rather than by hand;
> the steps remain accurate for rebuilding it in another workspace. Note the
> Lakehouse is **schema-enabled**, so the Gold tables live under the `dbo`
> schema (`dbo.module5_region_exposure`, etc.).

The analysis is plain pandas and runs identically on a laptop and in Fabric.
Fabric adds three things: somewhere to land the data, a schedule, and a
credential store. Nothing about the numbers changes.

```
Files/module5/               <- Bronze: the ICM extract, config.json, src/ or a wheel
  |
  v  nb_module5_capacity_denial   (ingest -> classify -> GATE -> price -> rank -> recommend)
  |
  +--> Tables/module5_*           Gold Delta tables (Power BI, other modules)
  +--> Files/module5/out/         the written finding the web app serves
```

## 1. Land the inputs

Upload to the Lakehouse attached to the notebook:

| Path | What |
|------|------|
| `Files/module5/Synthetic_ICM_Capacity_Data.xlsx` | ticket extract + reference sheets |
| `Files/module5/config.json` | policy (delay cut-off, top-N, caps) |
| `Files/module5/src/module5/` | the package, if not using a wheel |

Once the real ICM feed exists, point `ticket_source` at the Delta table it
lands in — `load_bronze` is the only function that needs to change, and it
already accepts a path.

## 2. Get the package into the session

Two options; pick one.

- **Wheel (preferred).** `python -m build` at the repo root, then attach
  `dist/fabric_capacity_intelligence-0.1.0-py3-none-any.whl` to a custom Fabric
  Environment and set that Environment on the notebook. Versioned, no per-run
  copy, no `sys.path` games.
- **Files copy.** Upload `src/module5/` to `Files/module5/src/module5/`. The
  notebook's first cell already puts that path on `sys.path`.

## 3. Secrets

None are stored in this repo or in the notebook. The notebook reads them from
Key Vault through the workspace identity:

| Secret name | Holds |
|-------------|-------|
| `module5-foundry-api-key` | Azure AI Foundry key — only if `use_llm=True` |

**Access model: access policies, not RBAC.** The deploying account holds
Contributor on `Rg-IPTool-Dev` only, so it cannot create RBAC role assignments
(that needs User Access Administrator). A vault with
`--enable-rbac-authorization false` lets Contributor grant data access directly
with `az keyvault set-policy`, which is how `kv-fabcap-ptg` is configured.

Fabric reads the secrets as the identity that owns the run, which is the account
that created the schedule — verified working on 2026-08-11. If ownership moves to
someone else, add a policy for them, or switch to a workspace identity (needs an
F-SKU capacity and an admin to enable it).
The Foundry endpoint (`https://mayus-mioz5b6n-eastus2.services.ai.azure.com/openai/v1`)
and deployment (`DeepSeek-V4-Flash`) are not secrets and sit in the notebook's
config cell. If Foundry is reachable with the workspace identity directly, skip
the key and set `AZURE_FOUNDRY_AD_TOKEN` instead — `llm.py` prefers a bearer
token over a key.

## 4. Import the notebook and pipeline

- Notebook: `fabric/notebooks/nb_module5_capacity_denial.py` → Import notebook,
  then attach the Lakehouse as default.
- Pipeline: `fabric/pipelines/module5_daily.json` → the workspace ID is already
  set; fill in `notebookId` (from the imported notebook's URL), `key_vault_name`
  and the ops alert target, then set the schedule in the UI.

Run it once. That writes the Gold tables and `Files/module5/out/finding.md`
— the written finding the application serves. Nothing is sent anywhere: the run
produces artefacts and the web application reads them, so a finding is reviewed
by opening it rather than by receiving it.

## 5. What the notebook publishes

| Table | Grain | Used by |
|-------|-------|---------|
| `module5_tickets_classified` | one row per ticket | audit, drill-through |
| `module5_region_exposure` | one row per region, ranked | the finding, Power BI |
| `module5_customer_exposure` | one row per affected subscription | account follow-up |
| `module5_exposure_trend` | one row per denial month | "is this getting worse?" |
| `module5_recommendations` | one row per top-N region | human review queue |

Tables are overwritten each run. That is correct for a fixed extract; when the
ICM feed becomes incremental, switch the write to a merge on
`(IncidentId, AsOf)`.

## 6. The gate

The notebook raises and fails the pipeline if the classifier does not reproduce
the pre-labelled sample exactly. That is deliberate: step 2 of the design doc
says to test the classifier before trusting it on real data, and a scheduled job
that quietly publishes numbers from a broken classifier is worse than one that
fails loudly.
