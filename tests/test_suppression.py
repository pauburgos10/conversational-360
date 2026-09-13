"""Small-N suppression — the tests the brief asks for by name.

These are written against a hand-built cell tree rather than seeded data so the
boundary conditions are exact and the failures are readable.
"""

import pytest

from poc.aggregation.rollup import Cell
from poc.aggregation.suppression import (
    CODE_COMPLEMENTARY, CODE_DOMINANCE, CODE_MIN_N, evaluate, summary,
)


def cell(key, parent=None, values=None, level="competency"):
    return Cell(
        key=key, scope="subject", scope_id="S1", level=level,
        level_id=key.split(":")[-1], label=key, parent_key=parent,
        values=values or [],
    )


def spread(n_ballots, per_ballot=1, value=2.0, prefix="b"):
    """n distinct ballots, each contributing `per_ballot` answers."""
    return [(f"{prefix}{i}", value) for i in range(n_ballots) for _ in range(per_ballot)]


# --------------------------------------------------------------------- min_n

@pytest.mark.parametrize("n,expect_suppressed", [(0, True), (1, True), (3, True), (4, False), (9, False)])
def test_min_n_boundary(rules, n, expect_suppressed):
    cells = {"c": cell("comp:C1", values=spread(n))}
    marks = evaluate(cells, rules)
    assert marks["c"].suppressed is expect_suppressed
    if expect_suppressed and n > 0:
        assert marks["c"].code == CODE_MIN_N


def test_min_n_counts_distinct_ballots_not_answers(rules):
    """One rater answering six items is one response, not six."""
    cells = {"c": cell("comp:C1", values=[("b0", 2.0)] * 6)}
    marks = evaluate(cells, rules)
    assert marks["c"].suppressed
    assert marks["c"].code == CODE_MIN_N


def test_reason_text_interpolates_threshold(rules):
    marks = evaluate({"c": cell("comp:C1", values=spread(2))}, rules)
    assert "fewer than 4" in marks["c"].reason


# ----------------------------------------------------------------- dominance

def test_dominance_suppresses_despite_meeting_min_n(rules):
    # 4 ballots, but one supplied 18 of the 21 answers.
    values = spread(1, per_ballot=18, prefix="big") + spread(3, per_ballot=1)
    cells = {"c": cell("dim:core", values=values, level="dimension")}
    marks = evaluate(cells, rules)
    assert marks["c"].suppressed
    assert marks["c"].code == CODE_DOMINANCE


def test_dominance_not_triggered_at_exactly_the_threshold(rules):
    # 3 + 3 answers -> max share is exactly 0.50, and the rule is strictly greater.
    values = spread(1, per_ballot=3, prefix="big") + spread(3, per_ballot=1)
    cells = {"c": cell("comp:C1", values=values)}
    marks = evaluate(cells, rules)
    assert not marks["c"].suppressed


def test_min_n_takes_precedence_over_dominance(rules):
    cells = {"c": cell("comp:C1", values=spread(1, per_ballot=10))}
    marks = evaluate(cells, rules)
    assert marks["c"].code == CODE_MIN_N


# ------------------------------------------------------------- complementary

def test_lone_suppressed_sibling_forces_a_second_suppression(rules):
    """The residual attack: parent published + one hidden child = child derivable."""
    cells = {
        "p":  cell("dim:core", values=spread(8), level="dimension"),
        "c1": cell("comp:C1", parent="dim:core", values=spread(8)),
        "c2": cell("comp:C2", parent="dim:core", values=spread(6)),
        "c3": cell("comp:C3", parent="dim:core", values=spread(2)),   # below min_n
    }
    marks = evaluate(cells, rules)
    assert not marks["p"].suppressed
    assert marks["c3"].code == CODE_MIN_N
    hidden = [k for k in ("c1", "c2", "c3") if marks[k].suppressed]
    assert len(hidden) >= 2, "a lone suppressed sibling is recoverable from the parent"
    assert marks["c2"].code == CODE_COMPLEMENTARY, "the least informative sibling is sacrificed"


def test_complementary_sacrifices_the_smallest_sibling(rules):
    cells = {
        "p":  cell("dim:core", values=spread(20), level="dimension"),
        "c1": cell("comp:C1", parent="dim:core", values=spread(12)),
        "c2": cell("comp:C2", parent="dim:core", values=spread(5)),
        "c3": cell("comp:C3", parent="dim:core", values=spread(9)),
        "c4": cell("comp:C4", parent="dim:core", values=spread(1)),
    }
    marks = evaluate(cells, rules)
    assert marks["c2"].suppressed and marks["c2"].code == CODE_COMPLEMENTARY
    assert not marks["c1"].suppressed
    assert not marks["c3"].suppressed


def test_no_complementary_when_two_siblings_already_hidden(rules):
    cells = {
        "p":  cell("dim:core", values=spread(8), level="dimension"),
        "c1": cell("comp:C1", parent="dim:core", values=spread(8)),
        "c2": cell("comp:C2", parent="dim:core", values=spread(2)),
        "c3": cell("comp:C3", parent="dim:core", values=spread(2)),
    }
    marks = evaluate(cells, rules)
    assert not marks["c1"].suppressed, "two hidden siblings already protect each other"


def test_no_complementary_when_the_parent_is_itself_suppressed(rules):
    cells = {
        "p":  cell("dim:core", values=spread(2), level="dimension"),
        "c1": cell("comp:C1", parent="dim:core", values=spread(8)),
        "c2": cell("comp:C2", parent="dim:core", values=spread(2)),
    }
    marks = evaluate(cells, rules)
    assert marks["p"].suppressed
    assert not marks["c1"].suppressed, "a hidden parent leaks no residual"


def test_complementary_cascades_to_a_fixed_point(rules):
    """Suppressing a sibling can leave ITS OWN children with a lone suppression."""
    cells = {
        "root": cell("overall:all", values=spread(10), level="overall"),
        "d1":   cell("dim:core", parent="overall:all", values=spread(10), level="dimension"),
        "d2":   cell("dim:behavioural", parent="overall:all", values=spread(2), level="dimension"),
        "d1c1": cell("comp:C1", parent="dim:core", values=spread(10)),
        "d1c2": cell("comp:C2", parent="dim:core", values=spread(3)),
    }
    marks = evaluate(cells, rules)
    assert marks["d2"].code == CODE_MIN_N
    assert marks["d1"].suppressed, "d1 was the lone visible sibling of a hidden d2"
    # d1 is now hidden, so its own children no longer need complementary cover.
    assert not marks["d1c1"].suppressed


def test_complementary_can_be_switched_off(rules):
    rules["suppression"]["complementary"] = False
    cells = {
        "p":  cell("dim:core", values=spread(8), level="dimension"),
        "c1": cell("comp:C1", parent="dim:core", values=spread(8)),
        "c2": cell("comp:C2", parent="dim:core", values=spread(2)),
    }
    marks = evaluate(cells, rules)
    assert not marks["c1"].suppressed


def test_evaluate_terminates_on_a_deep_chain(rules):
    """Guards against the fixed-point loop never settling."""
    cells = {}
    parent = None
    for i in range(40):
        key = f"n{i}"
        cells[key] = cell(f"level:{i}", parent=parent, values=spread(4 if i % 2 else 2))
        parent = f"level:{i}"
    marks = evaluate(cells, rules)
    assert len(marks) == len(cells)


# ------------------------------------------------------------------- export

def test_suppressed_cells_never_export_a_value(rules):
    from poc.reporting import aggregates_csv
    cells = {
        "a": cell("comp:C1", values=spread(8, value=2.5)),
        "b": cell("comp:C2", values=spread(2, value=1.0)),
    }
    marks = evaluate(cells, rules)
    csv = aggregates_csv(cells, marks, rules)
    hidden_row = [r for r in csv.splitlines() if "comp:C2" in r][0]
    assert ",TRUE," in hidden_row
    assert "1.0" not in hidden_row, "a suppressed cell leaked its mean into the export"


def test_summary_counts_every_cell(rules):
    cells = {
        "a": cell("comp:C1", values=spread(8)),
        "b": cell("comp:C2", values=spread(1)),
    }
    counts = summary(evaluate(cells, rules))
    assert counts["published"] + counts[CODE_MIN_N] + counts[CODE_DOMINANCE] + counts[CODE_COMPLEMENTARY] == 2
