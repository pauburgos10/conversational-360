"""What the two-store split still guarantees, after the demo-2 clarification.

The client's decision is that HR administers the round and may see who reviewed
whom and what they wrote. So the old claim these tests made — that *nobody* can
reconnect a response to a rater — is no longer the design, and asserting it here
would be asserting a fiction.

What is still true, and is what these tests hold, is narrower and worth keeping:

* `responses.db` carries no rater identity in any column. Attribution is a
  deliberate join against the identity side (`data/invites.json` here,
  `identity.db` in the full build), performed only by HR-facing report code.
* The subject's copy of a report attributes nothing, and open text reaching it is
  masked at render time.
* A rater's team, role and relationship still never travel with the response, so
  even an attributed report cannot be broken down by role.

The practical value: a leak of the response store alone discloses no reviewer,
and every attribution path is one that had to be written on purpose.
"""

import ast
import inspect
from pathlib import Path

import pytest

from poc import store
from poc.aggregation import rollup, suppression, themes
from poc.store import FORBIDDEN_COLUMNS


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "responses.db"
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    return path


def test_no_table_carries_rater_identity(db):
    """Adding a rater_id column later fails the build rather than leaking quietly."""
    schema = store.schema_columns(db)
    offenders = {
        table: sorted(set(cols) & FORBIDDEN_COLUMNS)
        for table, cols in schema.items()
        if set(cols) & FORBIDDEN_COLUMNS
    }
    assert offenders == {}, f"identity columns leaked into the response store: {offenders}"


def test_ballot_links_to_a_subject_and_nothing_else(db):
    columns = set(store.schema_columns(db)["ballot"])
    assert "subject_id" in columns
    assert not columns & FORBIDDEN_COLUMNS


def test_cohort_hangs_off_the_subject_not_the_ballot(db):
    """A subject's team is not a secret. A rater's team identifies them when
    cohorts are small, which is why it must not travel with the response."""
    schema = store.schema_columns(db)
    assert "cohort" in schema["subject"]
    assert "cohort" not in schema["ballot"]


def test_guardrail_events_record_a_code_but_never_the_text(db):
    columns = set(store.schema_columns(db)["guardrail_event"])
    assert columns == {"id", "ballot_id", "code", "at"}
    assert "text" not in columns and "content" not in columns


def test_fetch_round_exposes_no_rater_fields(db):
    store.create_ballot("S1", "R1", ballot_id="b1", path=db)
    store.save_submission("b1", {"C1.1": 2}, [{"ref": "Q1", "text": "clear and steady", "codes": []}], [], path=db)
    data = store.fetch_round("R1", path=db)
    for row in data["ballots"] + data["likert"] + data["open"]:
        assert not set(row) & FORBIDDEN_COLUMNS


def _module_ast(module):
    return ast.parse(inspect.getsource(module))


def test_aggregation_never_touches_the_store_or_the_roster():
    """The aggregation layer receives only what `fetch_round` hands it.

    Checked against the parse tree rather than the file text, so a mention in a
    docstring does not fail the build and a real call cannot pass it.
    """
    for module in (rollup, suppression, themes):
        tree = _module_ast(module)
        imported = {
            alias.name.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert "store" not in imported, f"{module.__name__} imports the store directly"

        opens = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"
        ]
        assert not opens, f"{module.__name__} reads a file of its own"


def test_conversation_state_holds_no_rater_identity():
    from poc.graph.state import InterviewState
    keys = set(InterviewState.__annotations__)
    assert not keys & FORBIDDEN_COLUMNS
    assert "ballot_id" in keys


def _collect_body():
    from poc.graph import nodes
    tree = ast.parse(inspect.getsource(nodes))
    outer = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "make_collect")
    return next(n for n in ast.walk(outer) if isinstance(n, ast.FunctionDef) and n.name == "collect")


def test_collect_scores_redaction_once_and_never_reaches_the_store():
    """One redaction call in `collect`, and no store access from it.

    `collect` no longer *replaces* the text — HR reads answers as written — but it
    still runs the redactor exactly once, to record which rules will fire when the
    subject's copy is rendered. Two calls would mean the rater's pre-submit preview
    and the subject's report were scored separately and could disagree; zero would
    mean the preview is guesswork.

    The store assertion is unchanged and still matters: persistence belongs to
    `finish`, so a half-finished interview cannot leave rows behind.
    """
    collect = _collect_body()
    names = {
        n.value.id
        for n in ast.walk(collect)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
    }
    assert "store" not in names, "collect must not persist; `finish` owns that"

    redactions = [
        n for n in ast.walk(collect)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "apply" and getattr(n.func.value, "id", "") == "_redactor"
    ]
    assert len(redactions) == 1, "exactly one redaction point, on the way in"


def test_only_finish_writes_to_the_store():
    from poc.graph import nodes
    tree = ast.parse(inspect.getsource(nodes))
    writers = set()
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and getattr(node.value, "id", "") == "store":
                writers.add(fn.name)
    assert writers <= {"finish"}, f"unexpected writers to the response store: {writers}"


# --------------------------------------------------------------- the asymmetry

def test_subjects_are_named_and_raters_are_not(db):
    """The boundary is one-directional, and that is the whole design.

    A development report is useless if you cannot tell whose it is, and the
    subject consented to being assessed. The raters did not consent to being
    identifiable, and structurally cannot be. Anonymising the subject too would
    be a misreading of the requirement, so it is pinned here.
    """
    schema = store.schema_columns(db)
    assert "display_name" in schema["subject"]
    assert "role" in schema["subject"]
    assert not set(schema["ballot"]) & FORBIDDEN_COLUMNS


def test_subject_role_is_kept_while_rater_relationship_is_forbidden():
    """Two different things that both sound like 'role'.

    The subject's job title is not secret. The rater's relationship to the
    subject is exactly what round 1 forbids reporting, which is why one is a
    column and the other is in FORBIDDEN_COLUMNS.
    """
    assert "role" not in FORBIDDEN_COLUMNS
    assert {"relationship", "relationship_to_subject"} <= FORBIDDEN_COLUMNS


def test_reports_identify_the_subject_and_say_who_is_anonymous(db):
    from poc import config, reporting
    from poc.aggregation import rollup, suppression

    store.upsert_subject("S9", "Nadia Okonkwo", "operations", "Legal & Immigration", path=db)
    for i in range(5):
        store.create_ballot("S9", "R1", ballot_id=f"n{i}", path=db)
        store.save_submission(f"n{i}", {"C1.1": 2, "C1.2": 2, "C1.3": 3}, [], [], path=db)

    data = store.fetch_round("R1", path=db)
    cells = rollup.build_cells(data)
    rules = config.rules()
    report = reporting.individual_report("S9", data, cells, suppression.evaluate(cells, rules), rules)

    assert "Nadia Okonkwo" in report
    assert "Legal & Immigration" in report
    assert "Who is named here" in report
