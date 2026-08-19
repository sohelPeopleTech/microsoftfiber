"""End-to-end orchestration for Module 5.

    ingest -> classify -> EVALUATE -> price -> rank -> recommend -> publish

The capitalised step is a gate, not a report. The design doc says to test the
classifier against the pre-labelled sample "before trusting it on real data",
so a failed evaluation blocks publication: the run still produces every
artefact for debugging, but the result carries `blocked=True` and the
application refuses to present it as a finding anyone should act on.

Locally this writes to out/. In Fabric the same functions run as notebook
stages against Lakehouse tables -- see fabric/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import aggregate, ingest, narrative, qa_pack, recommend, report, revenue, state
from .classifier import classify, category_counts, evaluate_against_labels
from .config import Config
from .llm import LLMConfig

DEFAULT_TICKETS = "data/Synthetic_ICM_Capacity_Data.xlsx"
DEFAULT_OUT = "out"


@dataclass
class PipelineResult:
    finding: dict
    priced: pd.DataFrame
    regions: pd.DataFrame
    recommendations: list
    markdown: str
    narrative: narrative.NarrativeResult | None = None
    blocked: bool = False
    blocked_reason: str = ""

    def to_dict(self) -> dict:
        return {
            **self.finding,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
        }


def run(
    ticket_source: str | Path = DEFAULT_TICKETS,
    subscription_source: str | Path | None = None,
    expected_source: str | Path | None = None,
    config: Config | None = None,
    out_dir: str | Path = DEFAULT_OUT,
    write_outputs: bool = True,
    use_llm: bool = False,
    llm_config: LLMConfig | None = None,
    state_dir: str | Path | None = None,
    run_at: str | None = None,
) -> PipelineResult:
    config = config or Config()
    out_dir = Path(out_dir)
    # State lives beside the outputs unless told otherwise, so a run always
    # remembers the last one without extra wiring.
    state_dir = Path(state_dir) if state_dir else out_dir / "state"

    # 0. Ingest -------------------------------------------------------------
    gold, dq = ingest.load_gold(ticket_source, subscription_source)

    # 1. Classify -----------------------------------------------------------
    classified = classify(gold, config)

    # 2. Evaluate against the labelled sample -- the gate -------------------
    evaluation: dict = {}
    blocked, blocked_reason = False, ""
    label_source = expected_source if expected_source is not None else ticket_source
    try:
        expected = ingest.load_expected_classifications(label_source)
        evaluation = evaluate_against_labels(classified, expected)
        if not evaluation["passed"]:
            blocked = True
            blocked_reason = (
                f"Classifier scored {evaluation['n_correct']}/{evaluation['n_scored']} "
                f"({evaluation['accuracy']:.0%}) against the labelled sample; "
                f"{len(evaluation['mismatches'])} mismatch(es). Publication blocked."
            )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        evaluation = {"skipped": True, "reason": str(exc)}

    # 3. Price --------------------------------------------------------------
    priced = revenue.estimate_impact(classified, config)

    # 4. Rank ---------------------------------------------------------------
    regions = aggregate.by_region(priced, config)
    summary = aggregate.portfolio_summary(priced, config)
    customers = aggregate.by_subscription(priced)
    trend = aggregate.exposure_trend(priced, config)

    # 5. Recommend ----------------------------------------------------------
    recommendations = recommend.recommend(regions, priced, config)

    dq_dict = dq.to_dict()
    dq_dict["summary_lines"] = dq.summary_lines()
    finding = report.build_finding(
        summary=summary,
        regions_ranked=regions.to_dict(orient="records"),
        recommendations=recommendations,
        data_quality=dq_dict,
        evaluation=evaluation,
        config=config,
    )
    # What changed since last time, and what a human already said about it.
    history = state.load_history(state_dir)
    prior = state.previous_run(history)
    finding["changes"] = state.compare_runs(prior, finding)
    decisions = state.latest_decisions(state.load_decisions(state_dir))
    finding["decisions"] = {
        rec.region: decisions[rec.region]
        for rec in recommendations
        if rec.region in decisions
    }
    prev_regions = (prior or {}).get("regions", {})
    all_decisions = state.load_decisions(state_dir)
    alert_states = {}
    for rec in recommendations:
        before = float(prev_regions.get(rec.region, {}).get("exposure", 0.0))
        st = state.alert_state(
            rec.region,
            rec.revenue_exposure_usd,
            before,
            decisions.get(rec.region),
            state.unanswered_cycles(history, all_decisions, rec.region),
        )
        alert_states[rec.region] = st
        rec.alert_state = st["state"]
        rec.decision_note = st["note"] or state.decision_note(decisions.get(rec.region))
    finding["alert_states"] = alert_states

    # A suppressed region stays in the data and out of the recommendation list.
    # Somebody already said no; repeating it is how a reader learns to ignore
    # the agent.
    recommendations = [r for r in recommendations if alert_states[r.region]["show"]]
    # build_finding() serialised the unfiltered list, so refresh it -- otherwise
    # finding.json and the application disagree about what was reported.
    finding["recommendations"] = [r.to_dict() for r in recommendations]

    finding["customers"] = customers.to_dict(orient="records")
    finding["trend"] = trend.to_dict(orient="records")
    finding["category_counts"] = category_counts(classified)
    if blocked:
        finding["status"] = "blocked_classifier_evaluation_failed"

    markdown = report.to_markdown(finding, recommendations)

    # 5b. Optional wording pass. Every figure is already final; the model only
    # rewrites prose, and a rewrite that mentions an unknown figure is dropped.
    narrated = narrative.write_narrative(
        finding=finding,
        recommendations=recommendations,
        fallback_text=report.headline_sentence(summary, recommendations),
        config=llm_config,
        enabled=use_llm,
    )
    finding["narrative"] = narrated.to_dict()

    # 6. Publish -- held for human review -----------------------------------
    #
    # The web application is the only delivery surface. It reads out/ and the
    # decisions log directly, so "delivery" is no longer a step that can fail
    # separately from the run: if the artefacts are written, the finding is
    # visible. A blocked run still writes them for debugging, and the blocked
    # flag travels on the result so the UI can refuse to present it as final.
    if write_outputs:
        _write_outputs(out_dir, finding, markdown, priced, regions, customers)
        state.record_run(state_dir, finding, run_at=run_at)

    result = PipelineResult(
        finding=finding,
        priced=priced,
        regions=regions,
        recommendations=recommendations,
        markdown=markdown,
        narrative=narrated,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )

    # Pre-computed answers for the in-app assistant to fall back on when the
    # LLM is unavailable. Built last -- it needs the finished result.
    if write_outputs:
        qa_pack.write(result, out_dir)
    return result


def _write_outputs(
    out_dir: Path,
    finding: dict,
    markdown: str,
    priced: pd.DataFrame,
    regions: pd.DataFrame,
    customers: pd.DataFrame,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "finding.json").write_text(json.dumps(finding, indent=2, default=str))
    (out_dir / "finding.md").write_text(markdown)
    regions.to_csv(out_dir / "region_exposure.csv", index=False)
    customers.to_csv(out_dir / "customer_exposure.csv", index=False)
    priced.to_csv(out_dir / "tickets_classified.csv", index=False)
