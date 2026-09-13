"""The self-assessment is a perspective, not a vote.

The client's model puts it explicitly outside the score: it exists so a manager
and a subject can compare views in the development conversation. These tests pin
that it never leaks into an average, and that keeping it separate does not
re-introduce the role marker that round 1 forbids on anonymous ballots.
"""

import pytest

from poc import config, reporting, store
from poc.aggregation import rollup, suppression
from poc.banks import load_bank
from poc.store import FORBIDDEN_COLUMNS

ITEMS = ["C1.1", "C1.2", "C1.3"]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "responses.db"
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    for i in range(5):
        store.create_ballot("S1", "R1", ballot_id=f"b{i}", path=path)
        store.save_submission(f"b{i}", {k: 2 for k in ITEMS}, [], [], path=path)
    return path


def cells_for(path):
    data = store.fetch_round("R1", path=path)
    return data, rollup.build_cells(data)


def test_self_ratings_do_not_move_the_average(db):
    _data, before = cells_for(db)
    key = "subject:S1|competency:C1"
    assert before[key].mean == 2.0

    # An extreme self-assessment that would visibly shift a 5-ballot mean.
    store.save_self_assessment("S1", "R1", {k: 3 for k in ITEMS}, path=db)

    _data, after = cells_for(db)
    assert after[key].mean == 2.0, "the self-assessment leaked into the aggregate"


def test_self_ratings_do_not_count_toward_min_n(db):
    """Otherwise a subject could unlock their own report by rating themselves."""
    store.save_self_assessment("S1", "R1", {k: 3 for k in ITEMS}, path=db)
    _data, cells = cells_for(db)
    assert cells["subject:S1|competency:C1"].n_ballots == 5


def test_self_ratings_arrive_in_their_own_key(db):
    store.save_self_assessment("S1", "R1", {k: 1 for k in ITEMS}, path=db)
    data = store.fetch_round("R1", path=db)
    assert data["self_assessments"]["S1"] == {k: 1 for k in ITEMS}
    assert all(row["ballot_id"] != "S1" for row in data["likert"])


def test_no_ballot_is_flagged_as_the_self_one(db):
    """Marking one ballot in a pool of three identifies the rest by elimination.
    Keeping the self-assessment in its own table avoids needing the flag at all."""
    columns = set(store.schema_columns(db)["ballot"])
    assert not columns & FORBIDDEN_COLUMNS
    assert not {"is_self", "self", "kind", "perspective"} & columns


def test_the_self_table_is_deliberately_attributable(db):
    """It holds the subject's own answers about themselves — no anonymity to protect."""
    columns = set(store.schema_columns(db)["self_assessment"])
    assert "subject_id" in columns
    assert not columns & FORBIDDEN_COLUMNS


# ------------------------------------------------------------------ reporting

def render(path, rules):
    data, cells = cells_for(path)
    return reporting.individual_report("S1", data, cells,
                                       suppression.evaluate(cells, rules), cells and rules)


def test_the_report_shows_the_gap(db):
    rules = config.rules()
    store.save_self_assessment("S1", "R1", {k: 3 for k in ITEMS}, path=db)
    data, cells = cells_for(db)
    report = reporting.individual_report("S1", data, cells,
                                         suppression.evaluate(cells, rules), rules)
    assert "Self-assessment vs. raters" in report
    assert "self rates higher" in report
    assert "Widest divergence" in report


def test_the_report_says_the_self_score_is_excluded(db):
    rules = config.rules()
    store.save_self_assessment("S1", "R1", {k: 2 for k in ITEMS}, path=db)
    data, cells = cells_for(db)
    report = reporting.individual_report("S1", data, cells,
                                         suppression.evaluate(cells, rules), rules)
    assert "not one of them" in report
    assert "Excluded from every figure above" in report


def test_a_suppressed_rater_figure_still_shows_the_self_score(tmp_path):
    """The subject already knows their own answer, so printing it reveals nothing."""
    path = tmp_path / "r.db"
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    store.create_ballot("S1", "R1", ballot_id="only", path=path)
    store.save_submission("only", {k: 1 for k in ITEMS}, [], [], path=path)
    store.save_self_assessment("S1", "R1", {k: 3 for k in ITEMS}, path=path)

    rules = config.rules()
    data, cells = cells_for(path)
    report = reporting.individual_report("S1", data, cells,
                                         suppression.evaluate(cells, rules), rules)
    assert "not comparable" in report
    assert "| Delivery | 3.00 | — |" in report


def test_a_missing_self_assessment_is_stated_not_hidden(db):
    rules = config.rules()
    data, cells = cells_for(db)
    report = reporting.individual_report("S1", data, cells,
                                         suppression.evaluate(cells, rules), rules)
    assert "No self-assessment was submitted" in report


def test_aggregation_code_never_mentions_the_self_table():
    import ast
    import inspect

    from poc.aggregation import rollup as r, suppression as sup, themes as th

    for module in (r, sup, th):
        tree = ast.parse(inspect.getsource(module))
        literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)}
        assert not any("self_assessment" in lit for lit in literals)


# ------------------------------------------------- graceful degradation

def under_threshold_report(tmp_path, n_raters):
    path = tmp_path / "r.db"
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    for i in range(n_raters):
        store.create_ballot("S1", "R1", ballot_id=f"b{i}", path=path)
        store.save_submission(f"b{i}", {k: 2 for k in ITEMS}, [], [], path=path)
    store.save_self_assessment("S1", "R1", {k: 3 for k in ITEMS}, path=path)
    rules = config.rules()
    data, cells = cells_for(path)
    return reporting.individual_report("S1", data, cells,
                                       suppression.evaluate(cells, rules), rules), rules


def test_a_subject_under_the_threshold_gets_an_explanation_not_a_page_of_dashes(tmp_path):
    """A table of suppressed cells invites a manager to read meaning into the
    pattern of what is hidden. Saying what happened is both clearer and safer."""
    report, rules = under_threshold_report(tmp_path, 1)
    assert "Not enough responses to report" in report
    assert "Where the ratings landed" not in report
    assert "It is a fact about the round" in report


def test_the_fallback_keeps_what_is_still_usable(tmp_path):
    report, _ = under_threshold_report(tmp_path, 1)
    assert "Self-assessment vs. raters" in report      # the subject's own data
    assert "Cohort context" in report                   # an anchor for the conversation
    assert "Next steps and commitments" in report       # the conversation still happens


def test_the_fallback_does_not_blame_the_subject(tmp_path):
    report, _ = under_threshold_report(tmp_path, 1)
    assert "not a finding about" in report.lower()


def test_at_the_threshold_the_full_report_renders(tmp_path):
    report, rules = under_threshold_report(tmp_path, rules_min_n := config.rules()["suppression"]["min_n"])
    assert "Not enough responses to report" not in report
    assert "Where the ratings landed" in report
    assert rules_min_n == 2


def test_the_report_warns_that_confidential_is_not_unidentifiable(tmp_path):
    """min_n=2 cannot deliver anonymity, so the report must not imply it does."""
    report, _ = under_threshold_report(tmp_path, 3)
    assert "confidential is not unidentifiable" in report.lower()


def test_consent_copy_matches_the_configured_threshold():
    """consent.md is Legal-owned and must not promise more than the config gives."""
    consent = config.consent_text()
    assert "fully anonymous" not in consent.lower()
    assert "confidential" in consent.lower()
    assert "may be able to work out who said what" in consent


def test_consent_copy_discloses_that_hr_can_attribute():
    """The copy must match what the code does, and the code now attributes.

    This is the test that would have caught the change going out silently. HR
    reading answers under a reviewer's name is only defensible if the reviewer
    was told before they wrote them; a stale consent screen turns a product
    decision into a broken promise.
    """
    # Whitespace-normalised: this is hand-wrapped prose, and an assertion that
    # breaks when Legal rewraps a paragraph is an assertion nobody will keep.
    consent = " ".join(config.consent_text().lower().split())
    assert "not anonymous to hr" in consent
    assert "as you wrote them" in consent and "your name against them" in consent

    # The specific promises the previous copy made, which are now false. The bare
    # word "nobody" is fine and still present — the copy uses it to retract the
    # old claim — so these match the claims themselves.
    assert "not your manager, not an administrator" not in consent
    assert "never stored alongside your answers" not in consent
    assert "no technical path to reconnect" not in consent
