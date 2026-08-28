# Capacity Intelligence Platform — Module 5

**Capacity-Denial Revenue Impact Calculator** (the pilot / build target).

When a capacity request is denied and only fixed later — or never fixed — that
shortfall sits in a support ticket with no roll-up, so nobody sees it as a
business risk. This module scans the tickets, separates *denied-then-approved-late*
from *denied-and-never-fulfilled*, sizes the affected customers' revenue, ranks
regions by exposure, and publishes a written recommendation into a web
application for a human to approve.

## Run it

```bash
pip install -e ".[dev]"          # or: PYTHONPATH=src
python -m module5 --config config.json
pytest -q
```

The run writes `out/` and sends nothing anywhere — the web application is the
only delivery surface, and it reads what the run wrote.

```bash
python -m module5 --ask "why is uksouth ranked lower?"
python -m module5 --llm               # LLM wording pass
python -m module5 --decisions         # who approved what, and why
```

### The application

```bash
cd webapp && python3 -m uvicorn api:app --port 8899
```

Six tabs — Overview, Regions, Customers, Propensity, Actions, Methodology —
behind a sign-in. Approve/Reject on the Actions tab writes to the same
append-only decisions log the CLI writes, and the next run suppresses a
rejected region. Set `APP_USERS` and `APP_SECRET_KEY` before anyone else can
reach it; unset, it serves a demo account and says so at startup.

## What comes out

`out/finding.md` — the written finding. Current run over the synthetic extract:

> **westeurope shows the highest revenue exposure this period — 4 request(s)
> were delayed or unfulfilled, affecting $690K in customer revenue ($76K at
> risk). Raise the auto-approval threshold in westeurope to 166 units.**

plus `finding.json` (machine-readable) and three CSVs (`region_exposure`,
`customer_exposure`, `tickets_classified`) — which are what the application
and the Gold Lakehouse tables are built from.

## How it works

| Step | Module | What it does |
|------|--------|--------------|
| 0 | `ingest.py` | Bronze → Silver → Gold, with a data-quality report |
| 1 | `classifier.py` | four outcomes from a date comparison, nothing else |
| 2 | `classifier.evaluate_against_labels` | **gate** — must reproduce the labelled sample or publication is blocked |
| 3 | `revenue.py` | ARR × capacity share × days unavailable ÷ 365 |
| 4 | `aggregate.py` | rank regions by exposure |
| 5 | `recommend.py` | one specific action per top region, with its arithmetic |
| 5b | `narrative.py` | optional LLM wording pass (Azure AI Foundry), grounding-checked |
| 6 | `webapp/` | serve the finding, held for human review |

Two figures are reported and they answer different questions. **ARR affected**
is the whole ARR of every affected customer, counted once each — blast radius.
**Revenue exposure** is risk-adjusted: only the share of capacity they were
missing, only for the days they went without it. Conflating them is how a
credible estimate becomes a number nobody trusts.

## Design decisions worth knowing

- **The classifier is a date comparison, not a model.** It is testable, and it
  is tested: 60/60 against the pre-labelled sample, plus boundary cases either
  side of the cut-off.
- **A failed gate blocks publication.** Artefacts are still written for
  debugging, but the result carries `blocked=True` and the application refuses
  to present it as a finding off a classifier that just got a known answer wrong.
- **The LLM never touches a number.** It rewrites prose from a computed payload
  (DeepSeek-V4-Flash via Azure AI Foundry). Any dollar figure not in its input,
  or any region written as a display name rather than its Azure identifier,
  fails the rewrite and the deterministic text is published instead. Aggregates the
  write-up needs — like the top-N subtotal — are computed and handed over, so
  the model never does arithmetic.
- **Nothing is executed automatically.** Every recommendation carries Approve /
  Reject, and every click is appended to `out/state/decisions.jsonl` with who,
  when and why. The next run suppresses a region somebody already rejected.
- **The dollars are illustrative today.** The ARR reference is placeholder data,
  and every output says so until `arr_reference_is_placeholder` is set false.

## Layout

```
src/module5/       the module (pure pandas; the LLM call over stdlib urllib)
src/module1..6/    the other modules, all views over src/dimensional/
src/propensity/    request-failure risk, scored at arrival
webapp/            the application -- FastAPI + vanilla JS, no build step
tests/             302 tests, ~4s
fabric/            notebook + Data Factory pipeline for the Fabric workspace
docs/CALIBRATION.md  where the 48-hour cut-off comes from
config.json        every number a reviewer might argue with
.env.example       every credential the module can use
```

Deployment into Fabric: [fabric/README.md](fabric/README.md).
