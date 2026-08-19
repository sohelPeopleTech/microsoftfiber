"""CLI: `python -m module5 [--ask "why is uksouth ranked lower?"]`

Writes artefacts to out/ and records a run in the history. The web application
is the delivery surface -- this command produces what it reads, and never
sends anything anywhere.
"""

from __future__ import annotations

import argparse
import sys

from . import agent, pipeline, state
from .config import Config
from .env import load_dotenv
from .llm import LLMConfig


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="module5",
        description="Capacity-Denial Revenue Impact Calculator (Module 5).",
    )
    p.add_argument("--tickets", default=pipeline.DEFAULT_TICKETS,
                   help="ICM extract (.xlsx or .csv)")
    p.add_argument("--subscriptions", default=None,
                   help="Subscription/ARR reference (defaults to the ticket workbook)")
    p.add_argument("--labels", default=None,
                   help="Pre-labelled sample for the classifier gate")
    p.add_argument("--config", default=None, help="config.json with policy overrides")
    p.add_argument("--out", default=pipeline.DEFAULT_OUT, help="output directory")
    p.add_argument("--ask", metavar="QUESTION",
                   help='follow-up question, e.g. "why is uksouth ranked lower?"')
    p.add_argument("--llm", action="store_true",
                   help="let Azure OpenAI word the finding (figures stay computed)")
    p.add_argument("--quiet", action="store_true", help="suppress the written finding")
    p.add_argument("--decide", choices=["approve", "reject"],
                   help="record a human decision on a region and exit")
    p.add_argument("--region", help="region the decision applies to")
    p.add_argument("--by", help="who made the decision")
    p.add_argument("--reason", default="", help="why (recorded verbatim, shown on reject)")
    p.add_argument("--decisions", action="store_true",
                   help="list the decisions recorded so far and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()  # local convenience; in Fabric the secrets come from Key Vault

    state_dir = f"{args.out}/state"
    if args.decisions:
        rows = state.load_decisions(state_dir)
        if not rows:
            print("No decisions recorded yet.")
        for row in rows:
            reason = f" -- {row['reason']}" if row.get("reason") else ""
            print(f"  {row['at'][:19]}  {row['region']:16} {row['decision']:8} "
                  f"by {row['by']}{reason}")
        return 0

    if args.decide:
        if not args.region or not args.by:
            print("--decide needs --region and --by", file=sys.stderr)
            return 2
        # as_of comes from the last run, so a decision attaches to the finding
        # the person actually looked at rather than to wall-clock time.
        history = state.load_history(state_dir)
        as_of = history[-1]["as_of"] if history else "unknown"
        row = state.record_decision(state_dir, as_of, args.region,
                                    args.decide, args.by, args.reason)
        print(f"Recorded: {row['region']} {row['decision']} by {row['by']} "
              f"for the period ending {as_of}.")
        return 0

    config = Config.load(args.config)

    result = pipeline.run(
        ticket_source=args.tickets,
        subscription_source=args.subscriptions,
        expected_source=args.labels,
        config=config,
        out_dir=args.out,
        use_llm=args.llm,
    )

    if not args.quiet:
        print(result.markdown)
        print()

    for line in result.finding["data_quality"]["summary_lines"]:
        print(line)
    print(f"Artefacts written to {args.out}/ -- open the web app to review them.")
    if args.llm:
        print(f"Narrative: {result.narrative.detail}")

    if args.ask:
        answer = agent.answer(
            args.ask,
            result,
            llm_config=LLMConfig.from_env() if args.llm else None,
            allow_llm=args.llm,
        )
        print(f"\nQ: {args.ask}\nA: {answer.text}")

    if result.blocked:
        print(f"\nBLOCKED: {result.blocked_reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
