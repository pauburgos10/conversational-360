"""The web layer, over HTTP.

Two kinds of test here, and the second is the one that matters in a year.

The smoke tests drive real requests through the real app: a whole interview,
resume across separate client sessions, a guardrail refusal, the RBAC gate, and
a suppressed cell rendering its reason. They exist because the web layer is the
one place where an engine change can look fine in `test_graph_flow.py` and still
produce a page that cannot be completed.

`test_rater_router_cannot_reach_the_reporting_side` is the structural one. The
repo already asserts that `poc/aggregation` cannot import the store (AST, not
docstrings); the same argument applies to the rater surface, which now sits in
the request path. A rater router that can call the reporting layer is one import
away from rendering someone else's cohort figures into a rater's page.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import deps, rounds
from app.main import app
from poc import banks, config, roster, store
from poc.session import Interview
from poc.graph import nodes
from tests.conftest import sign_in_as_hr, sign_in_as_rater

ROOT = Path(__file__).resolve().parent.parent


def n_items() -> int:
    """How many Likert items the bank holds.

    Derived, never typed. These assertions used to hardcode 26; realigning the
    instrument to the client's sections dropped it to 18 and broke eight tests
    that were each asserting a fact about the bank they could have asked for.
    """
    return len(banks.load_bank().all_items)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client on an isolated pair of databases.

    Both must move: `responses.db` for submissions and `checkpoints.db` for the
    conversation state. Pointing only one at tmp_path leaves the tests writing
    into the demo data, which is exactly the failure that makes a suite feel
    haunted.
    """
    monkeypatch.setattr(config, "RESPONSES_DB", tmp_path / "responses.db")
    monkeypatch.setattr(config, "CHECKPOINTS_DB", tmp_path / "checkpoints.db")
    monkeypatch.setattr(rounds, "INVITES_PATH", tmp_path / "invites.json")
    monkeypatch.setattr(rounds, "SELF_LINKS_PATH", tmp_path / "self_links.json")

    # The add-subject route appends to the roster CSV. Point it at a copy, or the
    # suite grows `inputs/subjects.csv` by a row every time it runs.
    csv_path = tmp_path / "subjects.csv"
    csv_path.write_text(
        "subject_id,display_name,role,cohort\n"
        "S1,Subject One,Team Lead,talent\n"
        "S2,Subject Two,Team Lead,talent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SUBJECTS_PATH", csv_path)
    roster.load_subjects.cache_clear()
    yield_redactor = nodes.refresh_redactor()
    assert yield_redactor is not None
    store.init()
    store.upsert_subject("S1", "Subject One", "talent", "Team Lead")
    store.upsert_subject("S2", "Subject Two", "talent", "Team Lead")
    # Signed in as the rater by default — the account a reviewer uses. Tests that
    # need HR switch with `as_hr`.
    with TestClient(app) as c:
        sign_in_as_rater(c)
        yield c

    # Leave the process as we found it: both the roster cache and the redactor
    # are module state, and a test that adds a subject would otherwise leak that
    # person into every later test in the run.
    roster.load_subjects.cache_clear()
    nodes.refresh_redactor()


def as_hr(client):
    return sign_in_as_hr(client)


def start(client, subject_id="S1") -> str:
    """Mint a link for `subject_id` the way HR does, and open it."""
    [ballot_id] = rounds.mint_invites(subject_id, 1)
    client.get(f"/b/{ballot_id}")
    return ballot_id


def start_without_invite(subject_id="S1") -> str:
    """A review begun from the removed "Start a review" button.

    Such ballots still exist on a deployment that predates minted-only links:
    they have a checkpoint but no invite. Created through the session directly,
    since no route can start one any more.
    """
    ballot_id = rounds.mint_ballot_id()
    Interview(ballot_id, subject_id).open()
    return ballot_id


def self_url(subject_id="S1") -> str:
    return f"/self/{rounds.mint_self_link(subject_id)}"


def reply(client, ballot_id, text):
    return client.post(f"/b/{ballot_id}/reply", data={"reply": text})


def run_to_end(client, ballot_id, answer="They rewrote the onboarding checklist last "
                                         "quarter, cutting ramp time from three weeks "
                                         "to one, and the team still uses it."):
    r = reply(client, ballot_id, "yes")
    for _ in range(80):
        if 'class="closing' in r.text:
            return r
        if 'class="num"' in r.text:
            r = reply(client, ballot_id, "2")
        elif "Submit</button>" in r.text:
            r = reply(client, ballot_id, "yes")
        elif "textarea" in r.text:
            r = reply(client, ballot_id, answer)
        else:
            break
    raise AssertionError("interview did not reach a closing state")


# ---------------------------------------------------------------------------
# the rater journey
# ---------------------------------------------------------------------------

def test_a_whole_interview_completes_over_http(client):
    ballot_id = start(client)
    r = run_to_end(client, ballot_id)

    assert "Submitted. Thank you." in r.text
    with store.connect() as conn:
        row = conn.execute(
            "SELECT status FROM ballot WHERE ballot_id=?", (ballot_id,)
        ).fetchone()
        n_likert = conn.execute(
            "SELECT COUNT(*) c FROM likert_answer WHERE ballot_id=?", (ballot_id,)
        ).fetchone()["c"]
    assert row["status"] == "submitted"
    assert n_likert == n_items()


def test_the_url_is_the_resume_handle(client):
    """Another browser, signed in afresh, continues the same conversation.

    The session says who is signed in; the link alone says which review.
    """
    ballot_id = start(client)
    reply(client, ballot_id, "yes")
    first = reply(client, ballot_id, "2")
    progress = re.search(rf">(\d+/{n_items()})<", first.text).group(1)

    with TestClient(app) as other:
        resumed = sign_in_as_rater(other).get(f"/b/{ballot_id}")
    assert resumed.status_code == 200
    assert "Resuming where you left off" in resumed.text
    assert progress in resumed.text


def test_reopening_the_link_does_not_advance_the_conversation(client):
    ballot_id = start(client)
    reply(client, ballot_id, "yes")
    reply(client, ballot_id, "2")

    first = client.get(f"/b/{ballot_id}").text
    second = client.get(f"/b/{ballot_id}").text
    pat = rf">(\d+/{n_items()})<"
    assert re.search(pat, first).group(1) == re.search(pat, second).group(1)


def test_an_hr_decision_question_is_refused_and_the_question_returns(client):
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    while 'class="num"' in r.text:
        r = reply(client, ballot_id, "2")

    r = reply(client, ballot_id, "Should I fire them? Their performance is poor.")
    assert "notice" in r.text
    assert "textarea" in r.text          # the same open question is re-presented

    # The refused turn is not an answer. Nothing persists before submit, so the
    # ballot row itself should not exist yet either.
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM open_answer").fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) c FROM ballot WHERE ballot_id=?", (ballot_id,)
        ).fetchone()["c"] == 0


def test_the_echo_shows_what_was_written_and_warns_about_the_subject_copy(client):
    """The bubble has to describe what actually happens, which is now two things.

    It used to echo the masked text and say the details were "removed before
    saving". Once masking moved to render time that was a false statement to the
    one person who cannot check it: the text IS saved, and HR does read it. So
    the echo shows their words, and the note scopes the removal to the subject's
    copy.
    """
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    while 'class="num"' in r.text:
        r = reply(client, ballot_id, "2")

    r = reply(client, ballot_id,
              "Ana Torres missed FEAT-231 and I chased her at ana@pluralit.com.")
    assert "ana@pluralit.com" in r.text, "the rater must see what was actually saved"
    assert "will be removed from the copy" in r.text
    assert "removed before saving" not in r.text, "the old, now-false wording"


def test_declining_consent_records_nothing(client):
    ballot_id = start(client)
    r = reply(client, ballot_id, "no")
    assert "You declined" in r.text
    with store.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM ballot WHERE ballot_id=?", (ballot_id,)
        ).fetchone()["c"]
    assert n == 0


def test_a_reply_to_an_unknown_ballot_is_refused(client):
    r = client.post("/b/never-existed/reply", data={"reply": "yes"})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# self-assessment
# ---------------------------------------------------------------------------

def test_self_assessment_saves_page_by_page_and_resumes(client):
    r = client.get(self_url())
    assert r.status_code == 200
    item_ids = re.findall(r'name="item_([^"]+)"', r.text)
    assert item_ids

    unique = list(dict.fromkeys(item_ids))
    client.post(self_url(), data={"page": 0, "round_id": rounds.DEFAULT_ROUND,
                                 **{f"item_{i}": "2" for i in unique}},
                follow_redirects=False)

    saved = rounds.self_answers("S1")
    assert set(saved) == set(unique)

    # reopening page 0 shows what was already answered
    again = client.get(self_url() + "?page=0")
    assert again.text.count("checked") >= len(unique)


def test_a_self_assessment_never_becomes_a_ballot(client):
    client.post(self_url(), data={"page": 0, "item_C1-1": "3"}, follow_redirects=False)
    with store.connect() as conn:
        ballots = conn.execute("SELECT COUNT(*) c FROM ballot").fetchone()["c"]
        selfs = conn.execute("SELECT COUNT(*) c FROM self_assessment").fetchone()["c"]
    assert selfs == 1
    assert ballots == 0


# ---------------------------------------------------------------------------
# the reporting side
# ---------------------------------------------------------------------------

def test_reports_require_the_hr_role(client):
    for path in ("/admin", "/admin/subject/S1", "/admin/cohort/talent", "/admin/aggregates.csv"):
        assert client.get(path).status_code == 403, path

    as_hr(client)
    assert client.get("/admin").status_code == 200


def test_a_subject_under_min_n_reports_the_reason_not_a_blank(client):
    """One ballot, min_n = 2 — the page must explain itself."""
    ballot_id = start(client, "S2")
    run_to_end(client, ballot_id)

    as_hr(client)
    r = client.get("/admin/subject/S2")
    assert r.status_code == 200
    assert "Not enough responses" in r.text

    landing = client.get("/").text
    assert "1 / 2" in landing          # the panel shows the count against the threshold


def test_aggregates_csv_downloads(client):
    as_hr(client)
    r = client.get("/admin/aggregates.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]


def test_freezing_records_a_digest(client):
    ballot_id = start(client)
    run_to_end(client, ballot_id)

    as_hr(client)
    client.post("/admin/freeze", data={"round_id": rounds.DEFAULT_ROUND},
                follow_redirects=False)
    info = rounds.round_freeze_info()
    assert info["ballot_n"] == 1
    assert len(info["digest"]) == 64


# ---------------------------------------------------------------------------
# the model: live when it can be, said plainly when it cannot
# ---------------------------------------------------------------------------
#
# There used to be an Offline / Live / Auto switch on the dashboard. The demo's
# value is the conversation the model conducts, so the switch is gone: with a key
# the app is live, and without one every page says so.

MODEL_BANNER = "The AI model is not available"


def test_the_model_runs_live_whenever_a_key_is_set(client, monkeypatch):
    """Asserted on `get_llm()`, not the page: a live-looking page handing out the
    stub client is the failure worth catching. The key is fake; nothing is called."""
    from poc import llm

    monkeypatch.delenv("C360_LLM_MODE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    assert deps.llm_status()["live"] is True
    assert type(llm.get_llm()).__name__ == "OpenAILLM"
    assert MODEL_BANNER not in client.get("/").text


def test_without_a_key_every_page_says_so(client, monkeypatch):
    monkeypatch.delenv("C360_LLM_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert deps.llm_status()["reason"] == "no_key"

    rater_page = client.get("/").text
    assert MODEL_BANNER in rater_page and "key is missing" in rater_page
    assert "contact your administrator" in rater_page
    as_hr(client)
    assert MODEL_BANNER in client.get("/admin").text


def test_there_is_no_mode_switch_any_more(client):
    as_hr(client)
    assert client.post("/admin/llm-mode", data={"mode": "stub"}).status_code in (404, 405)
    page = client.get("/admin").text
    assert "LLM mode" not in page
    assert "Offline" not in page


# ---------------------------------------------------------------------------
# the structural guard
# ---------------------------------------------------------------------------

FORBIDDEN_FOR_RATER = {
    "poc.reporting",
    "poc.aggregation",
    "poc.aggregation.rollup",
    "poc.aggregation.suppression",
    "poc.aggregation.themes",
    "app.routers.reports",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_rater_router_cannot_reach_the_reporting_side():
    """The rater surface must not be able to render aggregate figures.

    AST rather than a text search, so a mention in a docstring does not fail the
    build and a real import cannot pass it — the same standard
    `test_identity_separation.py` holds `poc/aggregation` to.
    """
    imported = _imported_modules(ROOT / "app" / "routers" / "rater.py")
    leaked = imported & FORBIDDEN_FOR_RATER
    assert not leaked, f"rater router imports the reporting side: {sorted(leaked)}"


def test_every_admin_route_is_role_gated():
    """A new report route must not be able to forget the dependency.

    The gate lives on the router, so this asserts the router is constructed with
    it rather than checking each path — a route added later inherits it, and
    someone removing it from the router fails here.
    """
    from app.routers import reports as reports_router
    from app.routers import subjects as subjects_router

    for name, module in (("reports", reports_router), ("subjects", subjects_router)):
        dependencies = module.router.dependencies
        assert dependencies, f"{name} admin router has no role dependency"
        assert any(
            getattr(d.dependency, "__name__", "") == "require_hr" for d in dependencies
        ), f"{name} admin router is not gated by require_hr"


# ---------------------------------------------------------------------------
# adding a subject
# ---------------------------------------------------------------------------

def test_hr_can_add_a_subject_and_it_appears_in_the_round(client):
    as_hr(client)
    r = client.post("/admin/subjects",
                    data={"display_name": "Sofia Marin", "role": "Analyst",
                          "cohort": "operations"},
                    follow_redirects=False)
    assert r.status_code == 303
    subject_id = r.headers["location"].split("added=")[1]

    # the roster CSV is the record
    rows = config.SUBJECTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert rows[-1] == f"{subject_id},Sofia Marin,Analyst,operations,,"  # no manager, no email

    # and the store, or reports would render an empty round
    with store.connect() as conn:
        row = conn.execute(
            "SELECT display_name, cohort FROM subject WHERE subject_id=?", (subject_id,)
        ).fetchone()
    assert row["display_name"] == "Sofia Marin"
    assert row["cohort"] == "operations"

    assert "Sofia Marin" in client.get("/").text


def test_a_new_subject_starts_below_min_n_and_says_so(client):
    """The whole point of adding one: watch it start empty."""
    as_hr(client)
    r = client.post("/admin/subjects", data={"display_name": "Sofia Marin"},
                    follow_redirects=False)
    subject_id = r.headers["location"].split("added=")[1]

    report = client.get(f"/admin/subject/{subject_id}").text
    assert "Not enough responses" in report

    row = next(s for s in rounds.subject_state() if s["subject_id"] == subject_id)
    assert row["n_ballots"] == 0
    assert row["publishable"] is False
    assert row["cohort"] == "unassigned"      # blank cohort must not become empty


def test_a_new_subjects_name_is_masked_in_the_subject_copy_immediately(client):
    """The reason the roster cache must be cleared, now checked where it matters.

    `roster.load_subjects` is `lru_cache`d. A subject added while the server runs
    is invisible to any redactor built from a stale read — so a rater naming them
    would have that name survive into the copy the subject receives.

    The check moved with the masking. It used to assert on the chat echo, which
    no longer masks anything; the render-time masker in `poc.reporting` is the
    thing that now has to know about Sofia, so that is what is asserted.
    """
    from poc import reporting

    as_hr(client)
    client.post("/admin/subjects", data={"display_name": "Sofia Marin"},
                follow_redirects=False)

    # both redactors: the one scoring codes on the way in, and the one masking on
    # the way out. A cache-clearing regression breaks them together.
    assert "Sofia Marin" in nodes.redactor().gazetteer
    masked = reporting._masker().apply("Sofia Marin unblocked the migration for us.")
    assert "Sofia Marin" not in masked.text
    assert "[NAME]" in masked.text


def test_a_duplicate_name_is_refused_rather_than_silently_added(client):
    as_hr(client)
    client.post("/admin/subjects", data={"display_name": "Sofia Marin"},
                follow_redirects=False)
    r = client.post("/admin/subjects", data={"display_name": "sofia marin"},
                    follow_redirects=False)

    assert "error=" in r.headers["location"]
    assert client.get(r.headers["location"]).text.count("Sofia Marin") >= 1
    names = [line.split(",")[1] for line in
             config.SUBJECTS_PATH.read_text(encoding="utf-8").strip().splitlines()[1:]]
    assert names.count("Sofia Marin") == 1


def test_a_new_subject_id_cannot_collide_with_existing_ballots(client):
    """Ids in the store are reserved even if no roster row mentions them."""
    store.upsert_subject("S9", "Ghost Subject", "talent", "")
    as_hr(client)
    r = client.post("/admin/subjects", data={"display_name": "Sofia Marin"},
                    follow_redirects=False)
    assert r.headers["location"].split("added=")[1] != "S9"


def test_adding_a_subject_requires_the_hr_role(client):
    r = client.post("/admin/subjects", data={"display_name": "Nope"},
                    follow_redirects=False)
    assert r.status_code == 403
    assert "Nope" not in config.SUBJECTS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# editing before submission
# ---------------------------------------------------------------------------

def _to_review(client, ballot_id):
    """Answer everything and stop on the review question."""
    r = reply(client, ballot_id, "yes")
    for _ in range(80):
        if "review" in r.text and "Submit</button>" in r.text:
            return r
        if 'class="num"' in r.text:
            r = reply(client, ballot_id, "2")
        elif "textarea" in r.text:
            r = reply(client, ballot_id,
                      "They rewrote the onboarding checklist last quarter, cutting "
                      "ramp time from three weeks to one, and the team still uses it.")
        else:
            break
    raise AssertionError("never reached review")


def test_the_likert_screen_promises_the_edit_is_possible(client):
    """The rater is told before they need it, not when they go looking."""
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    # Whitespace-normalised: the copy wraps in the template, so a raw substring
    # match would break on reflowing a sentence rather than on losing it.
    assert "change any rating at the review step" in " ".join(r.text.split())


def test_the_review_screen_lists_every_rating_and_answer(client):
    ballot_id = start(client)
    r = _to_review(client, ballot_id)

    assert f"{n_items()} ratings" in r.text
    assert "written answers" in r.text
    assert r.text.count('name="item_id"') == n_items()
    assert 'action="/b/' in r.text and "/edit/rating" in r.text


def test_editing_a_rating_changes_it(client):
    ballot_id = start(client)
    _to_review(client, ballot_id)

    iv = Interview(ballot_id, "S1")
    item = next(iter(iv._app.get_state(iv._cfg).values["likert"]))
    assert iv._app.get_state(iv._cfg).values["likert"][item] == 2

    r = client.post(f"/b/{ballot_id}/edit/rating",
                    data={"item_id": item, "value": 3}, follow_redirects=False)
    assert r.status_code == 303

    iv = Interview(ballot_id, "S1")
    assert iv._app.get_state(iv._cfg).values["likert"][item] == 3


def test_editing_a_rating_replans_the_probes_and_asks_only_what_is_new(client):
    """The trade-off we accepted: an edit can send the rater back for one probe.

    It must be exactly one. `next_question` skips anything already answered, so
    the re-planned list must not drag the rater back through probes they have
    already given — that would be the feature punishing them for correcting a
    typo.
    """
    from poc import banks

    ballot_id = start(client)
    _to_review(client, ballot_id)

    iv = Interview(ballot_id, "S1")
    before = {a["ref"] for a in iv._app.get_state(iv._cfg).values["open_answers"]}
    planned_before = [e["competency_id"]
                      for e in iv._app.get_state(iv._cfg).values["probe_plan"]]

    # Any competency the plan skipped will do. Named outright this was C6, which
    # stopped being unplanned when the instrument shrank from ten competencies
    # to six — the test then failed on its own premise rather than on the
    # behaviour it checks.
    target = next(c.id for c in banks.load_bank().competencies
                  if c.id not in planned_before)

    # Drive it to the bottom of the scale so it becomes the strongest signal.
    for item in banks.load_bank().competency(target).items:
        client.post(f"/b/{ballot_id}/edit/rating",
                    data={"item_id": item.id, "value": 1}, follow_redirects=False)

    iv = Interview(ballot_id, "S1")
    state = iv._app.get_state(iv._cfg).values
    # In the plan, not necessarily first: the plan is built in bank order, so a
    # competency earlier in the bank still precedes it. What matters is that the
    # edit put it there and that it is the question now on screen.
    assert target in [e["competency_id"] for e in state["probe_plan"]]
    assert state["pending"]["ref"] == f"probe:{target}"

    # Answering it returns straight to review, with nothing re-asked.
    r = reply(client, ballot_id,
              "They ship fixes without checking what the client actually needed first.")
    assert "Submit</button>" in r.text

    after = {a["ref"] for a in Interview(ballot_id, "S1")._app.get_state(iv._cfg).values["open_answers"]}
    assert after == before | {f"probe:{target}"}, "an already-answered probe was asked again"


def test_editing_a_written_answer_keeps_it_and_re_scores_the_mask(client):
    """An edit must behave exactly like a first answer: keep the text, note the codes.

    The risk this pins down is drift between the two write paths. `collect` and
    the edit route each build the answer record, and an edit that re-redacted
    while `collect` did not would silently destroy the one copy HR reads.
    """
    ballot_id = start(client)
    _to_review(client, ballot_id)

    client.post(f"/b/{ballot_id}/edit/answer",
                data={"ref": "Q1",
                      "text": "Ana Torres chased me at ana@pluralit.com over FEAT-231."},
                follow_redirects=False)

    iv = Interview(ballot_id, "S1")
    stored = next(a for a in iv._app.get_state(iv._cfg).values["open_answers"]
                  if a["ref"] == "Q1")
    assert "Ana Torres" in stored["text"]
    assert "ana@pluralit.com" in stored["text"]
    assert set(stored["codes"]) >= {"NAME", "EMAIL", "TICKET"}


def test_an_edit_after_submission_is_refused(client):
    """The review screen promises this cannot happen."""
    ballot_id = start(client)
    run_to_end(client, ballot_id)

    iv = Interview(ballot_id, "S1")
    item = next(iter(iv._app.get_state(iv._cfg).values["likert"]))
    before = iv._app.get_state(iv._cfg).values["likert"][item]

    r = client.post(f"/b/{ballot_id}/edit/rating",
                    data={"item_id": item, "value": 1}, follow_redirects=False)
    assert "notice=" in r.headers["location"]
    assert "cannot be changed" in client.get(r.headers["location"]).text

    iv = Interview(ballot_id, "S1")
    assert iv._app.get_state(iv._cfg).values["likert"][item] == before
    with store.connect() as conn:
        stored = conn.execute(
            "SELECT value FROM likert_answer WHERE ballot_id=? AND item_id=?",
            (ballot_id, item),
        ).fetchone()
    assert stored["value"] == before


def test_an_out_of_range_rating_is_refused(client):
    ballot_id = start(client)
    _to_review(client, ballot_id)
    iv = Interview(ballot_id, "S1")
    item = next(iter(iv._app.get_state(iv._cfg).values["likert"]))

    r = client.post(f"/b/{ballot_id}/edit/rating",
                    data={"item_id": item, "value": 9}, follow_redirects=False)
    assert "notice=" in r.headers["location"]
    assert Interview(ballot_id, "S1")._app.get_state(iv._cfg).values["likert"][item] == 2


# ---------------------------------------------------------------------------
# self-assessment navigation
# ---------------------------------------------------------------------------

def test_self_assessment_offers_back_and_a_jump_list(client):
    r = client.get(self_url() + "?page=2")
    assert 'name="goto" value="1"' in r.text, "no Back button"
    assert 'name="goto" value="0"' in r.text, "no jump target for page 0"
    assert "← Back" in r.text

    first = client.get(self_url() + "?page=0")
    assert "← Back" not in first.text, "page 0 must not offer Back"


def test_navigating_back_saves_the_current_page_first(client):
    """Moving around must never be the thing that loses an answer."""
    page1 = client.get(self_url() + "?page=1")
    ids = list(dict.fromkeys(re.findall(r'name="item_(\w[\w.]*)"', page1.text)))
    assert ids

    r = client.post(self_url(),
                    data={"page": 1, "goto": 0, **{f"item_{i}": "3" for i in ids}},
                    follow_redirects=False)
    assert r.headers["location"].endswith("page=0")

    saved = rounds.self_answers("S1")
    assert all(saved.get(i) == 3 for i in ids)


def test_a_previously_answered_page_comes_back_prefilled(client):
    ids = list(dict.fromkeys(re.findall(r'name="item_(\w[\w.]*)"',
                                        client.get(self_url() + "?page=0").text)))
    client.post(self_url(), data={"page": 0, **{f"item_{i}": "1" for i in ids}},
                follow_redirects=False)

    again = client.get(self_url() + "?page=0").text
    for item_id in ids:
        block = again.split(f'name="item_{item_id}"')
        assert 'value="1"' in block[1][:40] and "checked" in block[1][:60], item_id


def test_the_jump_list_shows_per_page_completion(client):
    ids = list(dict.fromkeys(re.findall(r'name="item_(\w[\w.]*)"',
                                        client.get(self_url() + "?page=0").text)))
    client.post(self_url(), data={"page": 0, **{f"item_{i}": "2" for i in ids}},
                follow_redirects=False)

    r = client.get(self_url() + "?page=1")
    assert f">{len(ids)}/{len(ids)}<" in r.text, "completed page not marked complete"
    assert ">0/" in r.text, "untouched page should show zero answered"


# ---------------------------------------------------------------------------
# what a ballot link is actually doing
# ---------------------------------------------------------------------------

def test_an_unopened_link_reads_as_not_opened(client):
    [ballot_id] = rounds.mint_invites("S1", 1)
    assert rounds.ballot_status(ballot_id) == rounds.BALLOT_NOT_OPENED


def test_a_consented_unfinished_interview_reads_as_in_progress(client):
    """The gap this closes: `responses.db` has no row for this rater at all."""
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    # Answer four RATINGS, stepping over any written question that interleaves.
    # Fixed at four replies, this broke when Delivery started earning a probe
    # immediately after its three items: the fourth reply answered the probe, so
    # only three ratings were recorded.
    answered = 0
    while answered < 4:
        if 'class="num"' in r.text:
            r = reply(client, ballot_id, "2")
            answered += 1
        else:
            r = reply(client, ballot_id,
                      "They caught the vendor delay in week 2 and re-sequenced the plan.")

    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM ballot WHERE ballot_id=?", (ballot_id,)
        ).fetchone()["c"] == 0, "precondition: nothing is in the store yet"

    state = rounds.ballot_states()[ballot_id]
    assert state.status == rounds.BALLOT_IN_PROGRESS
    assert state.answered == 4
    assert state.progress == f"4/{n_items()}"


def test_an_interview_stopped_before_consent_reads_as_not_opened(client):
    """Otherwise declining would visibly move a link backwards.

    If merely opening showed 'in progress', a decline would flip it to 'not
    opened' and anyone watching the page would learn a decline had happened.
    """
    ballot_id = start(client)
    client.get(f"/b/{ballot_id}")          # opened, sitting on the consent screen
    assert rounds.ballot_status(ballot_id) == rounds.BALLOT_NOT_OPENED


def test_declining_is_indistinguishable_from_never_opening(client):
    """consent.md: 'Declining closes the session and records nothing.'"""
    declined = start(client)
    r = reply(client, declined, "no")
    assert "You declined" in r.text

    [untouched] = rounds.mint_invites("S1", 1)
    assert rounds.ballot_status(declined) == rounds.ballot_status(untouched)
    assert rounds.ballot_status(declined) == rounds.BALLOT_NOT_OPENED


def test_a_crisis_halt_is_not_singled_out_on_the_invite_list(client):
    """A halted session must read like any other unfinished one.

    `guardrail_event` stores a code and never the text so distress cannot be
    attributed to a person. Flagging it per link would undo exactly that.
    """
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    while 'class="num"' in r.text:
        r = reply(client, ballot_id, "2")
    r = reply(client, ballot_id, "Some days I don't want to be here any more.")

    iv = Interview(ballot_id, "S1")
    assert iv._app.get_state(iv._cfg).values.get("halted") is True

    state = rounds.ballot_states()[ballot_id]
    assert state.status == rounds.BALLOT_IN_PROGRESS, "a halt must not get its own label"


def test_submitted_then_frozen_are_reported_distinctly(client):
    ballot_id = start(client)
    run_to_end(client, ballot_id)
    assert rounds.ballot_status(ballot_id) == rounds.BALLOT_SUBMITTED

    store.freeze_round(rounds.DEFAULT_ROUND)
    assert rounds.ballot_status(ballot_id) == rounds.BALLOT_FROZEN


def test_the_subject_panel_counts_interviews_in_flight(client):
    """This count was hardcoded to zero by a query that could never match."""
    before = next(s for s in rounds.subject_state() if s["subject_id"] == "S1")
    assert before["n_open"] == 0

    ballot_id = start(client)
    reply(client, ballot_id, "yes")
    reply(client, ballot_id, "2")

    after = next(s for s in rounds.subject_state() if s["subject_id"] == "S1")
    assert after["n_open"] == 1
    assert after["n_ballots"] == 0, "in flight is not the same as counted toward min_n"

    as_hr(client)
    assert "1 in progress" in client.get("/admin").text


def test_the_invite_list_shows_progress(client):
    [ballot_id] = rounds.mint_invites("S1", 1)
    client.get(f"/b/{ballot_id}")
    r = reply(client, ballot_id, "yes")
    for _ in range(3):
        r = reply(client, ballot_id, "2")

    as_hr(client)
    page = client.get("/admin/invites").text
    assert f"in progress · 3/{n_items()}" in " ".join(page.split())


# ---------------------------------------------------------------------------
# assigning a link to someone, so completion can be chased
# ---------------------------------------------------------------------------
#
# The demo feedback was that an anonymous list of links is not followable: HR
# could see two of three came back and had no way to know who to remind. These
# tests pin both halves of the answer — that the address is there to be used, and
# that having it does not let anyone read the answers behind it.

def test_a_link_can_be_minted_with_an_assignee(client):
    [ballot_id] = rounds.mint_invites("S1", 1, email="Rater.One@example.com")
    [invite] = rounds.invites_for()["S1"]
    assert invite.ballot_id == ballot_id
    assert invite.email == "Rater.One@example.com"
    assert invite.assigned


def test_a_link_minted_without_an_assignee_stays_unassigned(client):
    [ballot_id] = rounds.mint_invites("S1", 1)
    [invite] = rounds.invites_for()["S1"]
    assert invite.email == ""
    assert not invite.assigned
    assert rounds.subject_for_ballot(ballot_id) == "S1"


def test_an_address_can_be_attached_after_the_link_was_minted(client):
    """Minting and handing out happen days apart, so the two must be separable."""
    [ballot_id] = rounds.mint_invites("S1", 1)
    rounds.assign_email(ballot_id, "later@example.com")
    assert rounds.invites_for()["S1"][0].email == "later@example.com"


def test_an_address_can_be_corrected_and_cleared(client):
    [ballot_id] = rounds.mint_invites("S1", 1, email="typo@example.com")
    rounds.assign_email(ballot_id, "fixed@example.com")
    assert rounds.invites_for()["S1"][0].email == "fixed@example.com"

    rounds.assign_email(ballot_id, "")
    assert rounds.invites_for()["S1"][0].email == ""


def test_a_malformed_address_is_refused(client):
    with pytest.raises(ValueError):
        rounds.mint_invites("S1", 1, email="not-an-address")
    with pytest.raises(ValueError):
        rounds.normalise_email("two@@example.com")
    assert rounds.normalise_email("  spaced@example.com  ") == "spaced@example.com"


def test_assigning_an_unknown_link_is_an_error_not_a_silent_no_op(client):
    with pytest.raises(LookupError):
        rounds.assign_email("w-nosuchlink", "someone@example.com")


def test_links_minted_before_assignment_existed_still_load(client, tmp_path):
    """The pre-email file format is a bare list of ids. It must keep working.

    A round already in flight when this shipped would otherwise have every one of
    its links stop resolving to a subject.
    """
    (tmp_path / "invites.json").write_text(
        '{"2026-H2": {"S1": ["w-legacy-one", "w-legacy-two"]}}', encoding="utf-8"
    )
    invites = rounds.invites_for()
    assert [i.ballot_id for i in invites["S1"]] == ["w-legacy-one", "w-legacy-two"]
    assert all(i.email == "" for i in invites["S1"])
    assert rounds.subject_for_ballot("w-legacy-two") == "S1"

    # and an address can be added to one without rewriting the file by hand
    rounds.assign_email("w-legacy-one", "new@example.com")
    assert rounds.invites_for()["S1"][0].email == "new@example.com"
    assert rounds.invites_for()["S1"][1].ballot_id == "w-legacy-two"


def test_the_invite_page_mints_and_assigns_over_http(client):
    as_hr(client)
    client.post("/admin/invites",
                data={"subject_id": "S1", "n": 1, "email": "manager@example.com"},
                follow_redirects=False)
    [invite] = rounds.invites_for()["S1"]
    assert invite.email == "manager@example.com"

    client.post("/admin/invites/assign",
                data={"ballot_id": invite.ballot_id, "email": "someone.else@example.com"},
                follow_redirects=False)
    assert rounds.invites_for()["S1"][0].email == "someone.else@example.com"

    page = client.get("/admin/invites").text
    assert "someone.else@example.com" in page


def test_a_bad_address_over_http_reports_rather_than_500s(client):
    as_hr(client)
    r = client.post("/admin/invites", data={"subject_id": "S1", "n": 1, "email": "nope"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "does not look like an email" in r.text
    assert rounds.invites_for() == {}, "nothing should have been minted"


def test_the_invite_summary_counts_who_is_still_owed(client):
    rounds.mint_invites("S1", 1, email="done@example.com")
    rounds.mint_invites("S1", 1, email="waiting@example.com")
    rounds.mint_invites("S1", 1)

    ballot_id = rounds.invites_for()["S1"][0].ballot_id
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id)

    summary = rounds.invite_summary(rounds.invite_rows())
    assert summary["minted"] == 3
    assert summary["assigned"] == 2
    assert summary["unassigned"] == 1
    assert summary["complete"] == 1
    # the one number HR acts on: assigned, and not back yet
    assert summary["outstanding"] == 1


# ---------------------------------------------------------------------------
# reviews started without an invite can be handed back
# ---------------------------------------------------------------------------
#
# The former "Start a review" button minted a ballot id on the click and kept it
# only in the checkpoint. A reviewer who closed the tab mid-interview had no way
# back, and HR no list to find the link on. The button is gone, but ballots it
# started still exist on a deployment that predates it, so they stay recoverable.

def test_a_review_started_without_an_invite_is_listed_with_its_link(client):
    ballot_id = start_without_invite("S2")
    reply(client, ballot_id, "yes")
    reply(client, ballot_id, "2")

    as_hr(client)
    page = client.get("/admin/invites").text
    assert f'href="/b/{ballot_id}"' in page
    assert "Started without an invite" in page
    assert "1 in progress without an invite" in page


def test_the_recovered_link_resumes_the_same_review_about_the_same_person(client):
    """The listed link is the bare id; the subject comes from the checkpoint."""
    ballot_id = start_without_invite("S2")
    reply(client, ballot_id, "yes")
    reply(client, ballot_id, "2")

    [row] = [r for r in rounds.invite_rows() if r["ballot_id"] == ballot_id]
    page = client.get(row["link"]).text
    assert "Feedback on Subject Two" in page
    assert rounds.ballot_states()[ballot_id].answered == 1, "resumed, not restarted"


def test_a_bare_link_names_the_person_the_review_is_about(client):
    """A review from "Start a review" is not in the invite file. Its bare link
    used to fall back to S1 and head it "Feedback on" the wrong person."""
    ballot_id = start_without_invite("S2")
    reply(client, ballot_id, "yes")
    reply(client, ballot_id, "2")

    page = client.get(f"/b/{ballot_id}").text
    assert "Feedback on Subject Two" in page
    assert "Feedback on Subject One" not in page


def test_a_subject_in_the_link_cannot_relabel_an_existing_review(client):
    """Once a review exists, its checkpoint decides who it is about, not the URL."""
    ballot_id = start_without_invite("S2")
    reply(client, ballot_id, "yes")

    page = client.get(f"/b/{ballot_id}?subject=S1").text
    assert "Feedback on Subject Two" in page


def test_the_redirect_after_an_edit_keeps_the_right_person(client):
    """`_after_edit` redirects to the bare `/b/<id>` — the same fallback, unprompted."""
    ballot_id = start_without_invite("S2")
    _to_review(client, ballot_id)
    iv = Interview(ballot_id)
    item = next(iter(iv._app.get_state(iv._cfg).values["likert"]))

    r = client.post(f"/b/{ballot_id}/edit/rating", data={"item_id": item, "value": 3})
    assert "Feedback on Subject Two" in r.text


def test_a_minted_link_starts_about_the_subject_it_was_minted_for(client):
    """No checkpoint yet: the invite is what knows the subject."""
    assert "Feedback on Subject Two" in client.get(
        f"/b/{rounds.mint_invites('S2', 1)[0]}").text


def test_a_review_stopped_at_consent_or_declined_is_not_listed(client):
    """The consent promise: declining must read exactly like never opening."""
    at_consent = start_without_invite()
    declined = start_without_invite()
    reply(client, declined, "no")

    ids = {r["ballot_id"] for r in rounds.invite_rows()}
    assert at_consent not in ids
    assert declined not in ids


def test_reviews_without_an_invite_do_not_count_as_minted_links(client):
    """They were never minted or assigned, so they must not inflate those counts."""
    rounds.mint_invites("S1", 1)
    in_progress = start_without_invite()
    reply(client, in_progress, "yes")
    finished = start_without_invite()
    run_to_end(client, finished)

    rows = rounds.invite_rows()
    assert finished not in {r["ballot_id"] for r in rows}, "a submitted review needs no link"
    summary = rounds.invite_summary(rows)
    assert summary["minted"] == 1
    assert summary["unassigned"] == 1
    assert summary["in_progress"] == 0
    assert summary["started_directly"] == 1


def test_the_response_store_alone_cannot_attribute(client):
    """The boundary that survived the demo-2 clarification, stated precisely.

    HR may now attribute feedback — that is the client's decision — but the
    attribution is a deliberate join across two stores, not a column. Someone
    holding `responses.db` and nothing else still cannot say who wrote what.
    That is what keeps "identity separation" a real design rather than a label,
    and it is the property `identity.db` will carry in the full build.

    The earlier version of this test checked the same thing and claimed more:
    that the two could not be joined at all. They can — by `ballot_id` — which is
    exactly the mechanism `rater_labels()` now uses on purpose.
    """
    [ballot_id] = rounds.mint_invites("S1", 1, email="rater.identity@example.com")
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id)

    with store.connect() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        for table in tables:
            for row in conn.execute(f"SELECT * FROM {table}"):
                blob = " ".join(str(v) for v in tuple(row))
                assert "rater.identity" not in blob, f"identity leaked into {table}"


def test_hr_sees_who_said_what(client):
    """The demo-2 requirement, end to end."""
    [ballot_id] = rounds.mint_invites("S1", 1, email="marcos@example.com")
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id,
               answer="They quietly rebuilt the intake process and it has not slipped since.")

    as_hr(client)
    report = client.get("/admin/subject/S1").text
    assert "marcos@example.com" in report, "HR must see the reviewer"
    assert "quietly rebuilt the intake process" in report, "HR must see it as written"


def test_the_subject_copy_attributes_nothing(client):
    """Same round, same data, the other audience."""
    [ballot_id] = rounds.mint_invites("S1", 1, email="marcos@example.com")
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id,
               answer="Reach me on marcos@example.com and I will explain the delivery detail.")

    as_hr(client)
    subject_copy = client.get("/admin/subject/S1?audience=subject").text
    assert "marcos@example.com" not in subject_copy
    assert "Responses by reviewer" not in subject_copy
    assert "do not forward" not in subject_copy.lower()


def test_the_hr_copy_is_marked_not_for_the_subject(client):
    [ballot_id] = rounds.mint_invites("S1", 1, email="marcos@example.com")
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id)

    as_hr(client)
    report = client.get("/admin/subject/S1?audience=hr").text
    assert "do not forward" in report.lower(), "an attributed report must say so on its face"


def test_reporting_defaults_to_the_protective_audience(client):
    """A caller that forgets who is reading must get the masked rendering.

    This is the fail-closed property. `poc.reporting` is called from the web
    layer, the CLI, and tests; a new caller that omits `audience` should produce a
    duller report, never a disclosure.
    """
    from poc import reporting

    [ballot_id] = rounds.mint_invites("S1", 1, email="marcos@example.com")
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id,
               answer="Mail marcos@example.com for the specifics of that delivery.")

    pipe = rounds.pipeline()
    default = reporting.individual_report("S1", pipe.data, pipe.cells, pipe.marks,
                                          pipe.rules, pipe.bank)
    assert "marcos@example.com" not in default
    assert "Responses by reviewer" not in default


def test_an_unassigned_link_is_shown_as_such_not_hidden(client):
    """A response from a link nobody was recorded against is a distribution gap."""
    from poc import reporting

    [ballot_id] = rounds.mint_invites("S1", 1)          # deliberately no email
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id)

    pipe = rounds.pipeline()
    report = reporting.individual_report(
        "S1", pipe.data, pipe.cells, pipe.marks, pipe.rules, pipe.bank,
        audience=reporting.AUDIENCE_HR, raters=rounds.rater_labels())
    assert reporting.UNASSIGNED_RATER in report


def test_the_invite_page_still_shows_no_answer_content(client):
    """Attribution belongs on the report, not on the distribution list.

    The invite page exists to chase completion. Putting answers on it would mean
    anyone glancing at an HR screen reads someone's feedback.
    """
    [ballot_id] = rounds.mint_invites("S1", 1, email="rater.identity@example.com")
    client.get(f"/b/{ballot_id}")
    run_to_end(client, ballot_id, answer="They handle escalations calmly and early, in writing.")

    as_hr(client)
    page = client.get("/admin/invites").text
    assert "rater.identity@example.com" in page
    assert "escalations" not in page


# ---------------------------------------------------------------------------
# the self-assessment has three states, not two
# ---------------------------------------------------------------------------

def test_an_untouched_self_assessment_reads_as_not_started(client):
    state = rounds.self_status("S1")
    assert state["status"] == rounds.SELF_NOT_STARTED
    assert state["n_answered"] == 0
    assert not state["started"]


def test_a_partly_filled_self_assessment_reads_as_in_progress(client):
    """The gap this closes: a half-finished form used to look like an empty one."""
    ids = [i.id for i in banks.load_bank().competencies[0].items]
    client.post(self_url(), data={"page": 0, **{f"item_{i}": "2" for i in ids}},
                follow_redirects=False)

    state = rounds.self_status("S1")
    assert state["status"] == rounds.SELF_IN_PROGRESS
    assert state["started"] and not state["complete"]
    assert state["progress"] == f"{len(ids)}/{state['n_items']}"


def test_a_fully_answered_self_assessment_reads_as_complete(client):
    bank = banks.load_bank()
    for page, comp in enumerate(bank.competencies):
        client.post(self_url(),
                    data={"page": page, **{f"item_{i.id}": "2" for i in comp.items}},
                    follow_redirects=False)

    state = rounds.self_status("S1")
    assert state["status"] == rounds.SELF_COMPLETE
    assert state["complete"]
    assert state["n_answered"] == state["n_items"]


def test_the_round_pages_show_the_three_self_states(client):
    ids = [i.id for i in banks.load_bank().competencies[0].items]
    url = self_url()

    as_hr(client)
    assert "not started" in client.get("/admin").text

    client.post(url, data={"page": 0, **{f"item_{i}": "2" for i in ids}},
                follow_redirects=False)
    progress = f"in progress · {len(ids)}/{n_items()}"
    assert progress in " ".join(client.get("/admin").text.split())
    assert progress in " ".join(client.get("/admin/invites").text.split())


# ---------------------------------------------------------------------------
# the self-assessment is worded for the person reading it
# ---------------------------------------------------------------------------

def test_the_self_form_asks_in_the_second_person(client):
    page = client.get(self_url()).text
    assert "Do you consistently meet agreed deadlines" in page
    assert "Does this person" not in page, "rater wording on the subject's own form"


def test_the_self_form_anchors_are_in_the_second_person_too(client):
    """Question and level descriptions sit in one paragraph; both must match."""
    page = client.get(self_url()).text
    assert "You consistently meet what you agree to" in page
    assert "Requires frequent reminders" not in page


def test_the_rater_interview_keeps_the_third_person_wording(client):
    """The self wording must not leak the other way — a rater is not the subject."""
    ballot_id = start(client)
    page = reply(client, ballot_id, "yes").text
    assert "Do you consistently meet" not in page


def test_the_rater_is_asked_about_the_subject_by_first_name(client):
    """HR testing: "they" read as plural. The page must show the name, not the token."""
    ballot_id = start(client)
    page = reply(client, ballot_id, "yes").text
    assert "Does Subject consistently meet agreed deadlines" in page  # "Subject One"
    assert "{subject}" not in page


def test_every_item_carries_both_wordings(client):
    """A bank edit that adds an item and forgets `self_prompt` degrades quietly.

    `prompt_for` falls back on purpose so the page still renders, which is right
    at runtime and exactly why the omission needs catching here instead.
    """
    missing = [i.id for i in banks.load_bank().all_items if not i.self_prompt]
    assert not missing, f"items with no second-person wording: {missing}"


# ---------------------------------------------------------------------------
# "not observed" over HTTP
# ---------------------------------------------------------------------------

def test_the_scale_offers_the_opt_out(client):
    ballot_id = start(client)
    page = reply(client, ballot_id, "yes").text
    assert "not_observed" in page, "the button must post the canonical token"
    assert "no basis to rate this" in page


def test_the_opt_out_advances_instead_of_re_asking(client):
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    first = re.search(r'"reply": "not_observed"', r.text)
    assert first, "precondition: the opt-out is on screen"

    r = reply(client, ballot_id, "not_observed")
    iv = Interview(ballot_id, "S1")
    values = iv._app.get_state(iv._cfg).values
    assert values["declined"] == ["C1.1"]
    assert values.get("likert", {}) == {}
    assert values["pending"]["ref"] == "C1.2", "the walk must move on"


def test_a_declined_item_is_editable_at_the_review_step(client):
    """The last chance to change your mind, and the one people most want.

    This also covers the regression the opt-out introduced: `_guard_editable`
    counted only `likert`, so a single decline made the whole ballot uneditable
    and quietly removed the review step from the raters most likely to use it.
    """
    ballot_id = start(client)
    r = reply(client, ballot_id, "yes")
    r = reply(client, ballot_id, "not observed")        # decline C1.1

    for _ in range(120):                                # drive to review, don't submit
        if "Submit</button>" in r.text:
            break
        if 'class="num"' in r.text:
            r = reply(client, ballot_id, "2")
        elif "textarea" in r.text:
            r = reply(client, ballot_id,
                      "They rewrote the intake checklist and it has held up since.")
        else:
            break
    assert "Submit</button>" in r.text, "precondition: parked on the review screen"

    iv = Interview(ballot_id, "S1")
    assert iv._app.get_state(iv._cfg).values["declined"] == ["C1.1"]
    assert "not observed" in r.text.lower()

    client.post(f"/b/{ballot_id}/edit/rating", data={"item_id": "C1.1", "value": "3"},
                follow_redirects=False)

    iv = Interview(ballot_id, "S1")
    values = iv._app.get_state(iv._cfg).values
    assert values["likert"]["C1.1"] == 3
    assert "C1.1" not in values["declined"], "rating it must clear the decline"


def test_declines_show_as_coverage_on_the_report(client):
    """Not a low score and not missing data — a fact about visibility."""
    from poc import banks, reporting

    c3 = {i.id for i in banks.load_bank().competency("C3").items}
    for _ in range(2):
        ballot_id = start(client)
        r = reply(client, ballot_id, "yes")
        for _ in range(200):
            if 'class="closing' in r.text:
                break
            if 'class="num"' in r.text:
                ref = re.search(r'name="item_id" value="([^"]+)"', r.text)
                r = reply(client, ballot_id, "2")
            elif "Submit</button>" in r.text:
                r = reply(client, ballot_id, "yes")
            elif "textarea" in r.text:
                r = reply(client, ballot_id, "They steadied the intake process under pressure.")
            else:
                break

    pipe = rounds.pipeline()
    report = reporting.individual_report(
        "S1", pipe.data, pipe.cells, pipe.marks, pipe.rules, pipe.bank,
        audience=reporting.AUDIENCE_HR)
    # no declines in this round, so the section must not appear at all
    assert "no basis to judge" not in report


# ---------------------------------------------------------------------------
# who can do what: the rater and HR admin accounts
# ---------------------------------------------------------------------------
#
# The demo has two accounts. A rater reaches a review only through a link HR
# minted for one subject and sent them; everything else is HR's side.

def test_a_rater_cannot_open_any_admin_page(client):
    for path in ("/admin", "/admin/invites", "/admin/subject/S1", "/admin/aggregates.csv"):
        assert client.get(path).status_code == 403, path


def test_the_start_a_review_route_is_gone(client):
    """Reviews begin from minted links only; a rater cannot pick whom to review."""
    assert client.post("/start", data={"subject_id": "S1"}).status_code in (404, 405)


def test_a_link_nobody_minted_is_refused_and_starts_nothing(client):
    """It used to open a review "of" whoever was first on the roster."""
    r = client.get("/b/w-made-up-link")
    assert r.status_code == 404
    assert "link isn&#39;t valid" in r.text
    assert not Interview("w-made-up-link").exists, "no conversation may be created"


def test_hr_opening_a_ballot_is_told_it_counts(client):
    [ballot_id] = rounds.mint_invites("S1", 1)
    assert "answering as HR admin" not in client.get(f"/b/{ballot_id}").text
    as_hr(client)
    assert "answering as HR admin" in client.get(f"/b/{ballot_id}").text


def test_minting_points_hr_at_the_new_link(client):
    as_hr(client)
    r = client.post("/admin/invites", data={"subject_id": "S2", "n": 1})
    [ballot_id] = [i.ballot_id for i in rounds.invites_for()["S2"]]
    assert "1 link minted" in r.text
    assert 'class="fresh"' in r.text and f"/b/{ballot_id}" in r.text
    assert "Copy link" in r.text


def test_a_self_assessment_is_reached_only_by_its_minted_link(client):
    """Addressed by subject id, any signed-in rater could fill in anyone's form."""
    assert client.get("/self/S1").status_code == 404
    assert client.post("/self/S1", data={"page": 0, "item_C1.1": "3"}).status_code == 404
    assert rounds.self_answers("S1") == {}

    token = rounds.mint_self_link("S1")
    assert "Self-assessment" in client.get(f"/self/{token}").text


def test_minting_a_self_link_again_returns_the_one_already_sent(client):
    as_hr(client)
    assert "Mint self-assessment for Subject One" in client.get("/admin/invites").text
    client.post("/admin/self-links", data={"subject_id": "S1"})
    first = rounds.self_links()["S1"]
    client.post("/admin/self-links", data={"subject_id": "S1"})
    assert rounds.self_links()["S1"] == first
    page = client.get("/admin/invites").text
    assert f"/self/{first}" in page
    assert "Mint self-assessment for Subject One" not in page


# ---------------------------------------------------------------------------
# reporting lines
# ---------------------------------------------------------------------------

def test_a_person_can_be_added_reporting_to_someone_on_the_list(client):
    as_hr(client)
    r = client.post("/admin/subjects", data={"display_name": "Nora Field",
                                             "role": "Senior Developer",
                                             "cohort": "engineering",
                                             "manager_id": "S1"})
    assert "reporting to Subject One" in r.text
    [nora] = [s for s in roster.load_subjects() if s.display_name == "Nora Field"]
    assert nora.manager_id == "S1"
    assert "Reports to Subject One" in client.get("/admin").text


def test_the_manager_must_already_be_on_the_list(client):
    as_hr(client)
    r = client.post("/admin/subjects", data={"display_name": "Nora Field",
                                             "manager_id": "S99"})
    assert "not on the roster" in r.text
    assert all(s.display_name != "Nora Field" for s in roster.load_subjects())


def test_a_roster_from_before_reporting_lines_gains_the_column_intact(client):
    """The live roster on a deployment's disk has the old four-column header.

    Appending a longer row to it would leave the new columns unnamed; the file is
    rewritten with the full header and every existing row kept as is.
    """
    as_hr(client)
    client.post("/admin/subjects", data={"display_name": "Nora Field", "manager_id": "S2"})
    text = config.SUBJECTS_PATH.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "subject_id,display_name,role,cohort,manager_id,email"
    assert "S1,Subject One,Team Lead,talent,," in text
    assert text.rstrip().endswith(",S2,")


def test_a_bad_manager_in_the_file_is_dropped_not_fatal(tmp_path):
    """A failed load would empty the redaction list and unmask every name."""
    path = tmp_path / "subjects.csv"
    path.write_text("subject_id,display_name,role,cohort,manager_id\n"
                    "S1,Ana Torres,Lead,talent,S42\n"
                    "S2,Marcos Vidal,Sourcer,talent,S1\n", encoding="utf-8")
    roster.load_subjects.cache_clear()
    try:
        loaded = {s.subject_id: s for s in roster.load_subjects(path)}
    finally:
        roster.load_subjects.cache_clear()
    assert loaded["S1"].manager_id == ""
    assert loaded["S2"].manager_id == "S1"


def test_a_started_self_assessment_gets_its_link_without_anyone_minting_it(client):
    """The seeded round's self-assessments predate links; HR must still open them."""
    store.save_self_assessment("S1", rounds.DEFAULT_ROUND,
                               {i.id: 3 for i in banks.load_bank().all_items})
    assert "S1" not in rounds.self_links()

    as_hr(client)
    page = client.get("/admin/invites").text
    token = rounds.self_links()["S1"]
    assert f"/self/{token}" in page
    assert "Mint self-assessment for Subject One" not in page
    assert "Mint self-assessment for Subject Two" in page, "not started: HR decides"


def test_backfilling_is_idempotent_and_keeps_links_already_sent(client):
    store.save_self_assessment("S1", rounds.DEFAULT_ROUND, {"C1.1": 2})
    assert rounds.backfill_self_links() == ["S1"]
    token = rounds.self_links()["S1"]
    assert rounds.backfill_self_links() == []
    assert rounds.self_links()["S1"] == token


def test_hr_opening_a_self_assessment_sees_the_answers_and_is_warned(client):
    first = banks.load_bank().competencies[0].items[0].id
    store.save_self_assessment("S1", rounds.DEFAULT_ROUND, {first: 3})
    url = self_url()

    assert "viewing Subject One" not in client.get(url).text
    as_hr(client)
    page = client.get(url + "?page=0").text
    assert "viewing Subject One's self-assessment as HR admin" in page
    block = page.split(f'name="item_{first}" value="3"')[1][:30]
    assert "checked" in block, "the saved answer is shown"


# ---------------------------------------------------------------------------
# what stays on screen during the interview
# ---------------------------------------------------------------------------

def test_the_pinned_header_names_the_subject_and_promises_the_review_step(client):
    """Raters asked why they could not fix an earlier answer: the review step
    comes at the end, and the page now says so from the first question on."""
    ballot_id = start(client, "S2")
    page = " ".join(client.get(f"/b/{ballot_id}").text.split())
    sticky = page.split('class="chatsticky"')[1].split("</div> </div>")[0]
    assert "Feedback on Subject Two" in sticky
    assert "Bookmark this page" in sticky
    assert "You can change any answer at the end" in sticky


def test_every_page_offers_the_theme_toggle(client):
    """Including sign-in, the first page anyone sees."""
    assert 'id="theme-toggle"' in client.get("/").text
    with TestClient(app) as anon:
        assert 'id="theme-toggle"' in anon.get("/login").text


def test_the_dashboard_lists_people_in_reporting_line_order(client):
    """Each manager is followed by their own team, indented one level deeper."""
    as_hr(client)
    client.post("/admin/subjects", data={"display_name": "Nora Field", "manager_id": "S1"})
    client.post("/admin/subjects", data={"display_name": "Omar Haddad", "manager_id": "S2"})
    order = [(r["display_name"], r["depth"]) for r in rounds.subject_state()]
    assert order == [("Subject One", 0), ("Nora Field", 1),
                     ("Subject Two", 0), ("Omar Haddad", 1)]


def test_a_reporting_loop_in_the_file_still_lists_everyone_once(client):
    """A hand-edited loop must not hang the dashboard or drop anyone from it."""
    config.SUBJECTS_PATH.write_text("subject_id,display_name,role,cohort,manager_id\n"
                                    "S1,Subject One,Lead,talent,S2\n"
                                    "S2,Subject Two,Lead,talent,S1\n", encoding="utf-8")
    roster.load_subjects.cache_clear()
    names = [r["display_name"] for r in rounds.subject_state()]
    assert sorted(names) == ["Subject One", "Subject Two"]


# ---------------------------------------------------------------------------
# the person page: minting from the org chart
# ---------------------------------------------------------------------------

def _small_org(client):
    """S1 manages Nora and Omar; Nora manages Pia. Everyone but Omar has an email."""
    as_hr(client)
    for name, manager, email in (("Nora Field", "S1", "nora@example.com"),
                                 ("Omar Haddad", "S1", ""),
                                 ("Pia Lund", None, "pia@example.com")):
        if name == "Pia Lund":
            manager = next(s.subject_id for s in roster.load_subjects()
                           if s.display_name == "Nora Field")
        client.post("/admin/subjects", data={"display_name": name, "manager_id": manager,
                                             "email": email})
    return {s.display_name: s.subject_id for s in roster.load_subjects()}


def test_the_person_page_suggests_manager_reports_and_peers(client):
    ids = _small_org(client)
    page = " ".join(client.get(f"/admin/person/{ids['Nora Field']}").text.split())
    suggested = page.split("Suggested reviewers")[1].split("Someone else")[0]

    assert "Manager" in suggested and "Subject One" in suggested
    assert "Direct report" in suggested and "Pia Lund" in suggested
    assert "Peer" in suggested and "Omar Haddad" in suggested
    assert "Nora Field</strong>" not in suggested, "nobody reviews themselves"
    assert 'value="pia@example.com"' in suggested, "the roster address is pre-filled"


def test_minting_from_a_suggestion_assigns_the_address_and_returns_to_the_person(client):
    ids = _small_org(client)
    nora = ids["Nora Field"]
    r = client.post("/admin/invites", data={"subject_id": nora, "n": 1,
                                            "email": "pia@example.com",
                                            "back": f"/admin/person/{nora}"},
                    follow_redirects=False)
    assert r.headers["location"].startswith(f"/admin/person/{nora}?minted=")

    [invite] = rounds.invites_for()[nora]
    assert invite.email == "pia@example.com"
    page = " ".join(client.get(r.headers["location"]).text.split())
    assert "Link sent to pia@example.com" in page, "the suggestion shows it went out"
    assert 'class="fresh"' in page and f"/b/{invite.ballot_id}" in page


def test_a_link_can_go_to_someone_outside_the_organisation(client):
    ids = _small_org(client)
    nora = ids["Nora Field"]
    client.post("/admin/invites", data={"subject_id": nora, "n": 1,
                                        "email": "client@customer.example",
                                        "back": f"/admin/person/{nora}"})
    assert rounds.invites_for()[nora][0].email == "client@customer.example"


def test_the_relationship_is_never_stored_with_the_link(client):
    """Suggesting by relationship must not become reporting by relationship."""
    ids = _small_org(client)
    nora = ids["Nora Field"]
    client.post("/admin/invites", data={"subject_id": nora, "n": 1,
                                        "email": "pia@example.com",
                                        "back": f"/admin/person/{nora}"})
    stored = rounds.INVITES_PATH.read_text(encoding="utf-8").lower()
    for word in ("manager", "peer", "report", "relation"):
        assert word not in stored, word


def test_back_only_returns_to_admin_pages(client):
    as_hr(client)
    r = client.post("/admin/invites", data={"subject_id": "S1", "n": 1,
                                            "back": "//evil.example/x"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/admin/invites?")


def test_the_self_assessment_can_be_minted_from_the_person_page(client):
    as_hr(client)
    client.post("/admin/self-links", data={"subject_id": "S2", "back": "/admin/person/S2"})
    token = rounds.self_links()["S2"]
    assert f"/self/{token}" in client.get("/admin/person/S2").text


def test_an_unknown_person_is_a_404_and_a_rater_cannot_open_the_page(client):
    assert client.get("/admin/person/S1").status_code == 403
    as_hr(client)
    assert client.get("/admin/person/S99").status_code == 404


def test_a_malformed_roster_email_is_refused(client):
    as_hr(client)
    r = client.post("/admin/subjects", data={"display_name": "Nora Field", "email": "nope"})
    assert "does not look like an email" in r.text
    assert all(s.display_name != "Nora Field" for s in roster.load_subjects())
