"""Run the aggregation pipeline over a frozen round and write the reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import banks, config, reporting, store
from .aggregation import rollup, suppression


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate a round and render reports.")
    ap.add_argument("--round", default="2026-H2")
    ap.add_argument("--out", default="data/reports")
    ap.add_argument("--freeze", action="store_true", help="close the window and record a digest first")
    args = ap.parse_args()

    rules = config.rules()
    bank = banks.load_bank()

    if args.freeze:
        info = store.freeze_round(args.round)
        print(f"frozen: {info['ballot_n']} ballots · digest {info['digest'][:16]}…")

    data = store.fetch_round(args.round)
    cells = rollup.build_cells(data, bank)
    marks = suppression.evaluate(cells, rules)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for subject_id in sorted(data["subjects"]):
        text = reporting.individual_report(subject_id, data, cells, marks, rules, bank,
                                           audience=reporting.AUDIENCE_SUBJECT)
        (out_dir / f"individual_{subject_id}.md").write_text(text, encoding="utf-8")

    for cohort in sorted({s["cohort"] for s in data["subjects"].values()}):
        text = reporting.cohort_report(cohort, data, cells, marks, rules, bank,
                                       audience=reporting.AUDIENCE_SUBJECT)
        (out_dir / f"cohort_{cohort}.md").write_text(text, encoding="utf-8")

    (out_dir / "aggregates.csv").write_text(
        reporting.aggregates_csv(cells, marks, rules), encoding="utf-8"
    )

    summary = suppression.summary(marks)
    print(f"cells: {len(cells)}")
    for key, value in summary.items():
        print(f"  {key:<15} {value}")
    print(f"reports written to {out_dir}/")


if __name__ == "__main__":
    main()
