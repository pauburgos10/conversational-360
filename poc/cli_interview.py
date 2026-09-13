"""Terminal rater journey.

Stands in for the mobile web UI — and now runs beside it: both this and
`app/routers/rater.py` drive `poc.session.Interview`, so the terminal and the
browser cannot disagree about what consent accepts or when a follow-up fires.

The interesting property to demonstrate here is resume: kill the process
mid-interview with Ctrl-C, run the same command again with the same --ballot,
and the conversation continues from the exact question it was on, because the
checkpoint holds `pending`.
"""

from __future__ import annotations

import argparse
import textwrap

from . import trace
from .session import DEFAULT_ROUND, Interview

WRAP = 78


def _say(text: str, prefix: str = "") -> None:
    for para in str(text).split("\n"):
        if not para.strip():
            print()
            continue
        print(textwrap.fill(para, WRAP, initial_indent=prefix, subsequent_indent=prefix))


def _render(pending: dict) -> None:
    kind = pending["kind"]
    print()
    if kind == "consent":
        _say(pending["welcome"])
        print("\n" + "-" * WRAP)
        _say(pending["text"])
        print("-" * WRAP)
        print("\nDo you accept? [yes/no]")
        return

    if kind == "likert":
        print(f"[{pending['progress']}]  {pending['competency_name']} — {pending['label']}")
        _say(pending["text"])
        print()
        for value in ("1", "2", "3"):
            label = pending["scale"][value]
            print(textwrap.fill(f"  {value} = {label}", WRAP, subsequent_indent="      "))
        print("\n(type ? for the level descriptions)")
        return

    if kind == "review":
        _say(pending["text"])
        print("\n[yes/no]")
        return

    # Name the competency above an open probe. It sits directly under that
    # competency's ratings, and the heading says so rather than leaving the
    # rater to infer which answers the question is following up on.
    if pending.get("competency_name"):
        print(f"[{pending['competency_name']}]")
    _say(pending["text"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one rater conversation.")
    ap.add_argument("--ballot", required=True, help="ballot id — also the checkpoint thread id")
    ap.add_argument("--subject", default="S1")
    ap.add_argument("--round", default=DEFAULT_ROUND)
    ap.add_argument("--trace", action="store_true",
                    help="log what produced each question — model call, bank template, "
                         "or a fallback after an API error")
    args = ap.parse_args()

    if args.trace:
        trace.configure_logging()

    try:
        interview = Interview(args.ballot, args.subject, args.round)
        turn = interview.open()
    except LookupError as exc:
        raise SystemExit(str(exc))

    if turn.resumed:
        print(f"\n… resuming ballot {args.ballot} (stage: {turn.stage})")

    last_anchors: dict | None = None
    while not turn.finished:
        pending = turn.pending
        if pending.get("anchors"):
            last_anchors = pending["anchors"]
        _render(pending)
        try:
            reply = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSaved. Reopen the same link to carry on where you left off.")
            return

        if reply == "?" and last_anchors:
            print()
            for level in ("1", "2", "3"):
                _say(f"{level} — {last_anchors[level]}", prefix="  ")
            continue

        turn = interview.send(reply)
        if turn.notice:
            print()
            _say(turn.notice, prefix="  ")

    print("\n" + "=" * WRAP)
    outcome = turn.outcome
    if outcome == "declined":
        print("You declined. Nothing has been recorded. Thank you for your time.")
    elif outcome == "crisis":
        print("Session stopped. Your progress is saved.")
    elif outcome == "submitted":
        s = turn.summary
        print("Submitted. Thank you.")
        print(f"  {s['likert']} ratings · {s['open']} written answers · "
              f"{s['followups']} follow-ups asked")
        if s["redactions"]:
            print(f"  redacted before storage: {s['redactions']}")
    else:
        print("Saved, not submitted. Reopen the same link to finish.")
    print("=" * WRAP)


if __name__ == "__main__":
    main()
