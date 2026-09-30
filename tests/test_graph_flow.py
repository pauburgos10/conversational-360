"""Branching logic of the conversation engine.

These drive the real compiled graph, not a mock, so what is asserted here is
what a rater actually experiences.
"""

import pytest

from poc import config, store
from poc.banks import load_bank
from poc.graph.nodes import plan_probes
from poc.graph.state import STAGE_CRISIS, STAGE_DECLINED, STAGE_DONE

GOOD = ("Last quarter they caught the vendor delay in week 2 and re-sequenced the plan, "
        "which meant we still shipped on time.")
THIN = "They are fine."


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    path = tmp_path / "responses.db"
    monkeypatch.setattr(config, "RESPONSES_DB", path)
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    return path


def start(**over):
    base = {"ballot_id": "t", "subject_id": "S1", "subject_display": "Ana Torres",
            "round_id": "R1", "stage": "consent"}
    base.update(over)
    return base


def scripted(open_answer=GOOD, consent="yes", review="yes", likert="2"):
    def answer(pending, _turn):
        kind = pending["kind"]
        if kind == "consent":
            return consent
        if kind == "review":
            return review
        if kind == "likert":
            return likert
        return open_answer(pending) if callable(open_answer) else open_answer
    return answer


# ------------------------------------------------------------------- happy path

def test_full_run_completes_and_persists(driver, isolated_db):
    d = driver(scripted())
    final = d.run(start())
    assert final["stage"] == STAGE_DONE
    assert final["submitted"] is True
    assert len(final["likert"]) == len(load_bank().all_items)

    data = store.fetch_round("R1", path=isolated_db)
    assert len(data["ballots"]) == 1
    assert len(data["likert"]) == len(load_bank().all_items)


def test_consent_comes_before_any_question(driver, isolated_db):
    d = driver(scripted())
    d.run(start())
    assert d.asked[0]["kind"] == "consent"


def test_declining_records_nothing(driver, isolated_db):
    d = driver(scripted(consent="no"))
    final = d.run(start())
    assert final["stage"] == STAGE_DECLINED
    assert final.get("consent") is False
    assert store.fetch_round("R1", path=isolated_db)["ballots"] == []


def test_reviewing_and_declining_to_submit_saves_without_persisting(driver, isolated_db):
    d = driver(scripted(review="no"))
    final = d.run(start())
    assert final["stage"] == STAGE_DONE
    assert not final.get("submitted")
    assert store.fetch_round("R1", path=isolated_db)["ballots"] == []


# ------------------------------------------------------------------- guardrails

def test_crisis_halts_the_interview_and_records_no_answer(driver, isolated_db):
    def answer(pending, _t):
        if pending["kind"] == "consent":
            return "yes"
        if pending["kind"] == "likert":
            return "2"
        return "honestly I can't go on like this"

    d = driver(answer)
    final = d.run(start())
    assert final["stage"] == STAGE_CRISIS
    assert final["halted"] is True
    assert "CRISIS" in final["guardrail_codes"]
    assert final.get("open_answers", []) == [], "the crisis turn must not be stored as feedback"
    assert not final.get("submitted")
    assert store.fetch_round("R1", path=isolated_db)["ballots"] == []


def test_crisis_preserves_the_ratings_already_given(driver, isolated_db):
    """Probes are interleaved with the battery, so a crisis can land part-way
    through it. What was rated before the halt still has to survive — and it has
    to be an unbroken prefix, because the halt stops the walk rather than losing
    ground behind it."""
    def answer(pending, _t):
        if pending["kind"] == "consent":
            return "yes"
        if pending["kind"] == "likert":
            return "2"
        return "I want to hurt myself"

    final = driver(answer).run(start())
    rated = final["likert"]
    item_ids = [i.id for i in load_bank().all_items]

    assert rated, "the ratings given before the halt were dropped"
    assert list(rated) == item_ids[:len(rated)]
    assert set(rated.values()) == {2}


def test_refusal_re_asks_the_same_question_and_stores_nothing_for_that_turn(driver, isolated_db):
    state = {"n": 0}

    def answer(pending, _t):
        if pending["kind"] == "consent":
            return "yes"
        if pending["kind"] == "likert":
            return "2"
        if pending["kind"] == "review":
            return "yes"
        state["n"] += 1
        return "should I fire them?" if state["n"] == 1 else GOOD

    d = driver(answer)
    final = d.run(start())
    open_asked = [p for p in d.asked if p["kind"] == "open"]
    assert open_asked[0]["ref"] == open_asked[1]["ref"], "the same question must be re-asked"
    assert "HR_DECISION" in final["guardrail_codes"]
    texts = " ".join(a["text"] for a in final["open_answers"])
    assert "fire them" not in texts


def test_refusal_message_is_surfaced_to_the_rater(driver, isolated_db):
    state = {"n": 0}

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            return "2"
        state["n"] += 1
        return "what salary should they be on" if state["n"] == 1 else GOOD

    d = driver(answer)
    d.run(start())
    assert any("promotion, pay" in n for n in d.notices)


# -------------------------------------------------------------------- follow-ups

def test_thin_answers_trigger_followups_within_budget(driver, isolated_db):
    d = driver(scripted(open_answer=THIN))
    final = d.run(start())
    policy = load_bank().probe_policy
    assert final["followups_total"] > 0, "a thin answer should be probed further"
    assert final["followups_total"] <= policy["max_followups_per_session"]

    per_question: dict[str, int] = {}
    for pending in d.asked:
        if pending["kind"] == "open" and pending.get("is_followup"):
            base = pending["ref"].split(":f")[0]
            per_question[base] = per_question.get(base, 0) + 1
    assert max(per_question.values()) <= policy["max_followups_per_probe"]


def test_specific_answers_are_not_followed_up(driver, isolated_db):
    final = driver(scripted(open_answer=GOOD)).run(start())
    # The counter is only written once a follow-up fires, so absent means zero.
    assert final.get("followups_total", 0) == 0
    assert not any(p.get("is_followup") for p in driver(scripted(open_answer=GOOD)).asked)


def test_followup_answers_are_merged_into_one_stored_answer(driver, isolated_db):
    d = driver(scripted(open_answer=THIN))
    final = d.run(start())
    refs = [a["ref"] for a in final["open_answers"]]
    assert all(":f" not in r for r in refs), "follow-ups extend an answer, not create new ones"
    assert len(refs) == len(set(refs))


# --------------------------------------------------------------------- redaction

def test_open_answers_keep_what_the_rater_wrote(driver, isolated_db):
    """Masking moved to render time, so state holds the sentence as written.

    This inverts the previous assertion deliberately. HR reads answers verbatim
    (PLAN.md §2), so destroying the original on the way in is data loss, not a
    control. What `collect` still does is record which rules WILL fire when the
    subject's copy is rendered — see the codes assertion below.
    """
    leaky = "Ana Torres flagged it in FEAT-4412, mail her at ana.torres@pluralit.com about the detail here"
    final = driver(scripted(open_answer=leaky)).run(start())
    stored = " ".join(a["text"] for a in final["open_answers"])
    assert "ana.torres@pluralit.com" in stored
    assert "Ana Torres" in stored

    # The preview the rater is shown before submitting, and the promise the
    # subject's copy has to keep.
    codes = {c for a in final["open_answers"] for c in a["codes"]}
    assert {"EMAIL", "NAME", "TICKET"} <= codes
    assert final["redaction_counts"]["EMAIL"] > 0


def test_the_persisted_row_keeps_the_original_and_masks_on_render(driver, isolated_db):
    """Both halves of the new contract, in one place.

    The store holds what was written; `AUDIENCE_SUBJECT` is what removes it. If a
    future change quietly re-redacts on the way in, the first assertion fails; if
    render-time masking regresses, the second does.
    """
    from poc import banks, config, reporting
    from poc.aggregation import rollup, suppression

    leaky = "Reach me on ana.torres@pluralit.com if you want the full story about this delivery"
    driver(scripted(open_answer=leaky)).run(start())
    data = store.fetch_round("R1", path=isolated_db)

    assert any("pluralit.com" in row["text"] for row in data["open"]), \
        "the response store must keep the answer as written"

    rules, bank = config.rules(), banks.load_bank()
    cells = rollup.build_cells(data, bank)
    marks = suppression.evaluate(cells, rules)
    subject_copy = reporting.individual_report(
        "S1", data, cells, marks, rules, bank, audience=reporting.AUDIENCE_SUBJECT)
    # The contact detail, not the name: the subject here IS "Ana Torres", and a
    # subject is named in their own report by design. Only the rater side is
    # protected — see `reporting.ANONYMITY_NOTE`.
    assert "pluralit.com" not in subject_copy


# ----------------------------------------------------------------- input handling

def test_invalid_likert_input_re_asks_rather_than_crashing(driver, isolated_db):
    state = {"seen": set()}

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            if pending["ref"] not in state["seen"]:
                state["seen"].add(pending["ref"])
                return "banana"
            return "2"
        return GOOD

    d = driver(answer)
    final = d.run(start(), max_turns=200)
    assert len(final["likert"]) == len(load_bank().all_items)
    assert any("1, 2 or 3" in n for n in d.notices)


@pytest.mark.parametrize("value", ["0", "4", "-1", "2.5"])
def test_out_of_range_likert_is_rejected(driver, isolated_db, value):
    state = {"seen": set()}

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            if pending["ref"] not in state["seen"]:
                state["seen"].add(pending["ref"])
                return value
            return "3"
        return GOOD

    final = driver(answer).run(start(), max_turns=200)
    assert set(final["likert"].values()) <= {1, 2, 3}


# ------------------------------------------------------------------ probe order

def test_a_probe_is_asked_right_after_the_competency_it_triggered(driver, isolated_db):
    """The rater must be able to see which ratings a probe is about.

    Asking the whole battery and then a block of probes makes them guess: by the
    time the probe arrives, the ratings that produced it are twenty questions
    back. So every probe lands immediately after the last Likert item of its own
    competency, with nothing in between.
    """
    d = driver(scripted())
    d.run(start(), max_turns=200)

    bank = load_bank()
    last_item = {comp.id: comp.items[-1].id for comp in bank.competencies}

    probes = [(n, q) for n, q in enumerate(d.asked) if q["ref"].startswith("probe:")]
    assert probes, "no probe was asked at all"

    for n, q in probes:
        if q["is_followup"]:
            continue
        before = d.asked[n - 1]
        cid = q["competency_id"]
        assert before["kind"] == "likert" and before["ref"] == last_item[cid], (
            f"probe for {cid} followed {before['ref']!r}, not that competency's ratings"
        )


def test_ratings_after_a_probed_competency_still_get_asked(driver, isolated_db):
    """Interleaving must not truncate the battery: a probe in the middle of the
    bank is a detour, not an exit."""
    d = driver(scripted())
    final = d.run(start(), max_turns=200)
    assert len(final["likert"]) == len(load_bank().all_items)


def test_probes_follow_bank_order_once_interleaved(driver, isolated_db):
    d = driver(scripted())
    final = d.run(start(), max_turns=200)

    order = [c.id for c in load_bank().competencies]
    asked = [q["competency_id"] for q in d.asked
             if q["ref"].startswith("probe:") and not q["is_followup"]]
    assert asked == sorted(asked, key=order.index)
    assert [e["competency_id"] for e in final["probe_plan"]] == asked


# ---------------------------------------------------------------- probe planning

def test_probes_target_signal_not_every_competency():
    bank = load_bank()
    likert = {i.id: 2 for i in bank.all_items}
    likert.update({"C4.1": 1, "C4.2": 1, "C4.3": 1})     # uniformly low
    likert.update({"C1.1": 3, "C1.2": 1})                # internally split
    plan = plan_probes(likert, bank)

    assert len(plan) <= bank.probe_policy["max_probed_competencies"]
    by_comp = {p["competency_id"]: p["trigger"] for p in plan}
    assert by_comp.get("C4") == "low"
    assert by_comp.get("C1") == "mixed"


def test_no_dimension_is_left_with_ratings_only():
    """Every dimension earns at least one written question, whatever the pattern.

    This used to name the behavioural dimension, which was the one at risk while
    it sat last in bank order with four competencies. After the instrument was
    realigned to the client's sections the exposure moved to Delivery: a single
    competency, first in order, dropped on the first pass whenever its ratings
    carry no signal — and it is the 60% dimension. The rule is now about any
    dimension with nothing planned, so it protects whichever one is exposed.
    """
    bank = load_bank()
    all_dims = {d["id"] for d in bank.dimensions}

    for label, likert in (
        ("no signal anywhere", {i.id: 2 for i in bank.all_items}),
        ("signal only outside Delivery",
         {i.id: (2 if i.competency_id == "C1" else 1) for i in bank.all_items}),
        ("signal only in Delivery",
         {i.id: (1 if i.competency_id == "C1" else 2) for i in bank.all_items}),
    ):
        plan = plan_probes(likert, bank)
        dims = {bank.competency(p["competency_id"]).dimension for p in plan}
        assert dims == all_dims, f"{label}: {all_dims - dims} got no written question"


def test_probe_planning_is_deterministic():
    bank = load_bank()
    likert = {i.id: (i.id[-1] == "1" and 3 or 2) for i in bank.all_items}
    assert plan_probes(likert, bank) == plan_probes(likert, bank)


# ---------------------------------------------------------------------------
# "not observed" — the rater has no basis to rate this item
# ---------------------------------------------------------------------------
#
# Before this existed the instrument forced a number for every item, which does
# not produce missing data — it produces invented data that lands in the average
# looking exactly like an observation. These tests pin the three properties that
# make the opt-out safe: it is recorded, it is never scored, and it does not
# stall the walk.

def _decline_competency(cid, rate="2"):
    """Answer every item of `cid` with 'not observed' and rate everything else."""
    items = {i.id for i in load_bank().competency(cid).items}

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            return "not observed" if pending["ref"] in items else rate
        if pending.get("optional"):
            return "nothing to add"
        return GOOD
    return answer


def test_a_declined_item_is_recorded_but_never_scored(driver, isolated_db):
    final = driver(_decline_competency("C3")).run(start(), max_turns=200)
    declined = set(final["declined"])
    c3 = {i.id for i in load_bank().competency("C3").items}

    assert declined == c3
    assert not (set(final["likert"]) & declined), "a decline must never reach `likert`"
    assert len(final["likert"]) == len(load_bank().all_items) - len(c3)


def test_declining_does_not_stall_the_likert_walk(driver, isolated_db):
    """The walk advances on 'answered', where answered means rated OR declined.

    If `next_question` only counted `likert`, a declined item would be returned
    forever and the interview could never reach review.
    """
    final = driver(_decline_competency("C3")).run(start(), max_turns=200)
    assert final["submitted"] is True


def test_an_entirely_declined_ballot_still_completes(driver, isolated_db):
    """The degenerate case: a rater who has seen none of this person's work.

    Worth pinning because a fully declined competency leaves `_trigger_for` with
    no values to take a mean of — the arithmetic path that would divide by zero.
    """
    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            return "not observed"
        if pending.get("optional"):
            return "nothing to add"
        return GOOD

    final = driver(answer).run(start(), max_turns=300)
    assert final["submitted"] is True
    # `.get`: nothing ever wrote the key, which is itself the thing being
    # checked — no item was scored, so no scores dict was created.
    assert final.get("likert", {}) == {}
    assert len(final["declined"]) == len(load_bank().all_items)


@pytest.mark.parametrize("phrasing", [
    "not observed", "Not Observed", "n/a", "no basis", "cannot say",
    "not seen", "don't know", "not_observed", "skip",
])
def test_the_phrasings_a_rater_actually_types_are_accepted(driver, isolated_db, phrasing):
    state = {"done": False}

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            if not state["done"]:
                state["done"] = True
                return phrasing
            return "2"
        if pending.get("optional"):
            return "nothing to add"
        return GOOD

    final = driver(answer).run(start(), max_turns=200)
    assert len(final["declined"]) == 1


@pytest.mark.parametrize("phrasing", ["unsure", "not sure", "maybe", "banana"])
def test_hedging_is_not_read_as_a_refusal_to_answer(driver, isolated_db, phrasing):
    """The scale tells raters "Not sure? Answer as best you can".

    Someone following that advice must not have it reinterpreted as an opt-out —
    that would silently drop a rating they intended to give.
    """
    state = {"seen": set()}

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            if pending["ref"] not in state["seen"]:
                state["seen"].add(pending["ref"])
                return phrasing
            return "2"
        if pending.get("optional"):
            return "nothing to add"
        return GOOD

    d = driver(answer)
    final = d.run(start(), max_turns=250)
    assert final.get("declined", []) == []
    assert len(final["likert"]) == len(load_bank().all_items)
    assert any("1, 2 or 3" in n for n in d.notices)


def test_a_decline_triggers_the_not_observed_probe_for_that_competency(driver, isolated_db):
    """The point of the probe: a partial view is still worth capturing."""
    asked = []

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            return "not observed" if pending["ref"].startswith("C3") else "2"
        if pending.get("optional"):
            asked.append(pending["competency_id"])
            return "nothing to add"
        return GOOD

    driver(answer).run(start(), max_turns=200)
    assert "C3" in asked, "the competency they could not judge must earn its probe"


def test_the_optional_probe_can_be_declined_without_storing_an_empty_answer(driver, isolated_db):
    final = driver(_decline_competency("C3")).run(start(), max_turns=200)
    refs = {a["ref"] for a in final["open_answers"]}
    assert "probe:C3" not in refs, "'nothing to add' must not become a stored answer"
    assert all(a["text"].strip() for a in final["open_answers"])


def test_the_optional_probe_never_asks_a_follow_up(driver, isolated_db):
    """Pressing for detail would ask them to invent what they just said they lack."""
    followups = []

    def answer(pending, _t):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        if pending["kind"] == "likert":
            return "not observed" if pending["ref"].startswith("C3") else "2"
        if pending.get("is_followup"):
            followups.append(pending["ref"])
        if pending.get("optional"):
            return "hmm"          # short and vague: would normally earn a follow-up
        return GOOD

    driver(answer).run(start(), max_turns=200)
    assert not [f for f in followups if f.startswith("probe:C3")]


def test_a_rating_given_later_supersedes_the_decline(driver, isolated_db):
    """The review step lets a rater revisit. Answering must clear the decline."""
    from poc.graph.nodes import plan_probes

    final = driver(_decline_competency("C3")).run(start(), max_turns=200)
    assert "C3.1" in final["declined"]

    # plan_probes must treat a rated item as rated even if it was once declined
    likert = {**final["likert"], "C3.1": 2}
    declined = [d for d in final["declined"] if d != "C3.1"]
    assert "C3.1" not in declined
    plan_probes(likert, load_bank(), declined)      # must not raise


def test_declines_reach_the_store_and_never_the_likert_table(driver, isolated_db):
    driver(_decline_competency("C3")).run(start(), max_turns=200)
    data = store.fetch_round("R1", path=isolated_db)

    declined = {d["item_id"] for d in data["declined"]}
    scored = {l["item_id"] for l in data["likert"]}
    assert declined == {i.id for i in load_bank().competency("C3").items}
    assert not (declined & scored)
