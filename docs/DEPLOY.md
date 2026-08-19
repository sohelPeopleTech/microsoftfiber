# Module 5 — click-by-click deployment

Five phases. Each one ends with something you can see working, so if a phase
fails you know exactly which one broke. Do them in order.

| Phase | What you get | Time |
|-------|--------------|------|
| 0 | The analysis running on your laptop | 2 min |
| 1 | The application running, behind a sign-in | 15 min |
| 2 | It running inside Fabric, writing tables | 30 min |
| 3 | It running on a schedule | 15 min |
| 4 | Secrets in Key Vault, real accounts | 20 min |

Nothing is sent to anyone at any point — the run writes artefacts and the
application reads them. Nothing runs unattended until Phase 3. You can stop
after any phase and pick it up later.

---

## Phase 0 — Run it on your laptop (2 minutes)

Open Terminal, then:

```bash
cd ~/fabric-capacity-intelligence
python3 -m pytest -q
```

**Expect:** `81 passed`. If you see that, the analysis is correct — the
classifier reproduced all 60 known answers.

```bash
PYTHONPATH=src python3 -m module5 --config config.json
```

**Expect:** a written finding, starting with westeurope having the highest
exposure, then `Artefacts written to out/ -- open the web app to review them.`

Open `out/finding.md` to read the full thing. **Nothing was sent anywhere.**

✅ *Phase 0 done when you've read `out/finding.md`.*

---

## Phase 1 — Run the application (15 minutes)

The analysis is only half of it. This is the half people actually look at, and
it is worth standing up on your laptop first, where the feedback loop is
seconds rather than a pipeline run.

### 1.1 Start it

```bash
cd ~/fabric-capacity-intelligence/webapp
python3 -m uvicorn api:app --port 8899
```

**Expect:** two warnings — no `APP_USERS`, no `APP_SECRET_KEY`. Both are
correct at this point and both are fixed in Phase 4.

Open **http://127.0.0.1:8899** and sign in with `capacity` / `fabric2026`.

### 1.2 Walk the six tabs

| Tab | What to check |
|-----|---------------|
| Overview | The KPI strip and the outcome funnel |
| Regions | Move the safety threshold — the count of actionable regions changes, because it is re-running Module 1, not filtering |
| Customers | Click a customer; every request shows its own arithmetic |
| Propensity | Read the red banner before the scores — the model reports that it cannot yet support decisions |
| Actions | The recommendations, with Approve / Reject |
| Methodology | Every formula and the provenance table |

### 1.3 Record a decision

On **Actions**, reject a recommendation with a reason, then:

```bash
cd ~/fabric-capacity-intelligence
PYTHONPATH=src python3 -m module5 --decisions
```

**Expect:** your click, listed from the command line. The application and the
CLI append to the same `out/state/decisions.jsonl`, which is the audit trail.
Run the pipeline again and the rejected region drops out of the
recommendations — a decision changes the next run, which is the whole point of
recording it.

### 1.4 Try the LLM wording (optional)

```bash
PYTHONPATH=src python3 -m module5 --config config.json --llm
```

Same finding, written by DeepSeek instead of by the template. Compare the two
and keep whichever you prefer — it is a flag, not a commitment.

✅ *Phase 1 done when you have signed in, walked the tabs, and seen your own
Approve/Reject in `--decisions`.*

---

## Phase 2 — Run it inside Fabric (30 minutes)

### 2.1 Open the workspace

Go to **https://app.fabric.microsoft.com/groups/f78efe69-fe5e-494b-8433-77d828725ad1**

That's your Fabric Accelerators workspace. Bookmark it.

### 2.2 Create the Lakehouse (skip if it exists)

1. Click **+ New item** (top left).
2. Search for and choose **Lakehouse**.
3. Name it exactly: `Fabric_Capacity_Intelligence`
4. Click **Create**.

> The name must match exactly — the notebook checks it and refuses to run if
> it's attached to the wrong one. That's on purpose: a notebook pointed at the
> wrong Lakehouse reads nothing and writes tables nobody is looking at.

### 2.3 Upload the data and the code

You're now inside the Lakehouse, with **Tables** and **Files** on the left.

1. Hover **Files** → click the **⋯** → **New subfolder** → name it `module5`.
2. Hover the new `module5` folder → **⋯** → **Upload** → **Upload files**.
3. Choose these two from `~/fabric-capacity-intelligence`:
   - `data/Synthetic_ICM_Capacity_Data.xlsx`
   - `config.json`
4. Click **Upload**.
5. Now the code. Hover `module5` → **⋯** → **Upload** → **Upload folder**.
6. Choose the folder `~/fabric-capacity-intelligence/src` — the whole `src`
   folder, not its contents.
7. Confirm the browser prompt asking to upload multiple files.

When done, `Files/module5/` should contain `Synthetic_ICM_Capacity_Data.xlsx`,
`config.json`, and `src/module5/` with about a dozen `.py` files in it.

### 2.4 Import the notebook

1. Go back to the workspace (click its name in the left sidebar).
2. Click **Import** → **Notebook** → **From this computer**.
3. Click **Upload** and choose:
   `~/fabric-capacity-intelligence/fabric/notebooks/nb_module5_capacity_denial.ipynb`
4. Wait for the "imported successfully" notification, then click the notebook
   name to open it.

### 2.5 Attach the Lakehouse

The notebook cannot see any data until you do this.

1. In the notebook, look at the **Explorer** panel on the left.
2. Click **+ Data sources** (or **Add lakehouse**).
3. Choose **Existing Lakehouse** → **Add**.
4. Tick `Fabric_Capacity_Intelligence` → **Add**.

It should now appear in the Explorer with **Tables** and **Files** under it.

### 2.6 Run it

Click **Run all** at the top.

The first cell prints which workspace and Lakehouse it resolved — check the
Lakehouse line says `Fabric_Capacity_Intelligence`.

**Expect**, over about 2–3 minutes:
- `module5 loaded from /lakehouse/default/Files/module5/src/module5/pipeline.py`
- `Data quality: clean (60 tickets).`
- `Dry run -- payload written to ...` (posting is **off** by default here)
- five lines like `module5_region_exposure: 11 row(s)`
- the full written finding
- three example follow-up questions and their answers

### 2.7 Check the tables

1. In the Explorer, hover **Tables** → **⋯** → **Refresh**.
2. You should see five: `module5_tickets_classified`, `module5_region_exposure`,
   `module5_customer_exposure`, `module5_exposure_trend`,
   `module5_recommendations`.
3. Click `module5_region_exposure` to preview it — 11 regions, ranked, with
   westeurope at the top.

✅ *Phase 2 done when you can see those five tables with data in them.*

### If a cell fails

| Error | Fix |
|-------|-----|
| `No module named 'module5'` | The `src` upload in 2.3 didn't land. Check `Files/module5/src/module5/pipeline.py` exists. |
| `No default Lakehouse attached` | Redo 2.5. |
| `Attached to Lakehouse 'X' but expects...` | Wrong Lakehouse attached — remove it in Explorer and re-add the right one. |
| `Ticket extract not found` | The `.xlsx` isn't at `Files/module5/`. Redo 2.3 step 3. |

---

## Phase 3 — Put it on a schedule (15 minutes)

### 3.1 Get the notebook's ID

1. With the notebook open, look at the browser address bar. It reads:
   `.../synapsenotebooks/<A-LONG-GUID>?experience=...`
2. Copy that GUID — that's your `notebookId`.

### 3.2 Create the pipeline

1. Back in the workspace: **+ New item** → **Data pipeline**.
2. Name it `PL_Module5_CapacityDenialRevenueImpact` → **Create**.
3. Click **Add pipeline activity** → **Notebook**.
4. Click the activity, then the **Settings** tab at the bottom.
5. **Notebook**: choose `nb_module5_capacity_denial`.
6. Expand **Base parameters** and click **+ New** for each:

   | Name | Type | Value |
   |------|------|-------|
   | `use_llm` | Bool | `false` |

7. Click **Save** (top left).

`fabric/pipelines/module5_daily.json` in the repo is the same thing as JSON if
you'd rather import it — but clicking it in is faster than editing GUIDs.

### 3.3 Test it

Click **Run**. Watch the activity go green. This is the same run as Phase 2,
just triggered by the pipeline instead of by you.

### 3.4 Schedule it

1. In the pipeline, click **Schedule** (top ribbon).
2. **On**.
3. **Repeat**: Weekly · **Monday** · **08:00** · your timezone.
4. **Apply**.

> Weekly matches the "this period" framing in the design doc. Daily is fine
> later, once the ICM feed is live rather than a fixed extract.

✅ *Phase 3 done when the pipeline runs green and the schedule shows "On".*

---

## Phase 4 — Secrets in Key Vault, real accounts (20 minutes)

Two things are still wrong for anything beyond your own laptop: the Foundry key
lives in a `.env`, and the application is serving a demo account whose password
is in this repository.

### 4.1 Mint real accounts

For each person who should have access:

```bash
cd ~/fabric-capacity-intelligence
python3 -c "import sys; sys.path.insert(0,'webapp'); import auth; \
            print(':'.join(('alice',) + auth.hash_password('their-password')))"
```

That prints one `username:salt:hash` entry. Join them with commas into
`APP_USERS`. The password itself is never stored — only a PBKDF2-SHA256 hash
with a per-user salt.

Also set `APP_SECRET_KEY` to a long random string:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Without it, every restart silently signs everyone out.

### 4.2 Store the secrets

In the **Azure portal** (portal.azure.com), in your Key Vault
(create one if there isn't one — **Create resource** → *Key Vault*):

1. **Objects** → **Secrets** → **+ Generate/Import**.
2. Name `module5-foundry-api-key`, Value = your Foundry key → **Create**.
3. Repeat for `module5-app-secret-key` and `module5-app-users`.

### 4.3 Let Fabric read them

1. In the Key Vault → **Access control (IAM)** → **+ Add** → **Add role
   assignment**.
2. Role: **Key Vault Secrets User** → **Next**.
3. **Members** → **Managed identity** → select your Fabric workspace identity.
4. **Review + assign**.

### 4.4 Turn on the LLM

Back in the Fabric pipeline (3.2), edit the Base parameters:

| Name | Value |
|------|-------|
| `key_vault_name` | your vault's name, e.g. `kv-fabric-capacity` |
| `use_llm` | `true` if you preferred the LLM wording in 1.4 |

**Save**, then **Run** once.

### 4.5 Serve the application over TLS

One line to change before anyone else can reach it. In
[webapp/api.py](../webapp/api.py), in `login_submit`, set `secure=True` on the
session cookie. It is off by default so the pilot works over plain HTTP on an
internal address, where a secure cookie would simply vanish.

✅ *Phase 4 done when the demo account no longer works and the startup warnings
are gone.*

---

## Rotate the Foundry key

The key was pasted into a chat transcript, so treat it as exposed. In the
Azure AI Foundry portal → your resource → **Keys and Endpoint** → **Regenerate
Key 1**, then update `.env` locally and the `module5-foundry-api-key` secret.

## What each phase depends on

```
Phase 0  (nothing)
Phase 1  Phase 0
Phase 2  Fabric workspace + Lakehouse
Phase 3  Phase 2
Phase 4  Phase 3 + an Azure Key Vault
```

Phase 4 is the only one needing anything from an Azure admin. Phases 0–3 you
can do entirely on your own.
