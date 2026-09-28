"""Rater questions name the subject instead of saying "they".

HR testing found that readers working in a second language took "they" as
plural. The bank now carries a `{subject}` placeholder, filled with the first
name as each question is asked. The failure these pin is the placeholder
reaching a rater's screen as literal `{subject}`, or a bank edit quietly
dropping the name again.
"""

import pytest

from poc import banks, config, llm, store
from poc.banks import NO_NAME, first_name, fill_subject, load_bank
from poc.graph.state import STAGE_DONE

GOOD = ("Last quarter they caught the vendor delay in week 2 and re-sequenced the plan, "
        "which meant we still shipped on time.")
THIN = "Fine."


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    path = tmp_path / "responses.db"
    monkeypatch.setattr(config, "RESPONSES_DB", path)
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    return path


def start():
    return {"ballot_id": "t", "subject_id": "S1", "subject_display": "Ana Torres",
            "round_id": "R1", "stage": "consent"}


# ------------------------------------------------------------------ the helper

def test_first_name_is_the_first_word_of_the_display_name():
    assert first_name("Ana Torres") == "Ana"
    assert first_name("  Marcos  ") == "Marcos"
    assert first_name("") == ""
    assert first_name(None) == ""


def test_fill_subject_puts_the_name_in_and_leaves_other_braces_alone():
    text = "Does {subject} own {competency}? Beyond {subject}'s tasks, {x}."
    assert fill_subject(text, "Ana") == "Does Ana own {competency}? Beyond Ana's tasks, {x}."


def test_the_fallback_is_capitalised_only_where_a_sentence_starts():
    """"this person seems…" must not open a question in lower case."""
    text = "{subject} seems strong. Where does {subject} slip? {subject}'s impact."
    assert fill_subject(text, "") == (
        "This person seems strong. Where does this person slip? This person's impact."
    )


# ------------------------------------------------------------------- the bank

def test_every_rating_question_names_the_subject():
    missing = [i.id for i in load_bank().all_items if banks.SUBJECT_TOKEN not in i.prompt]
    assert not missing, f"rating questions that fell back to a pronoun: {missing}"


def test_every_probe_and_closing_question_names_the_subject():
    bank = load_bank()
    missing = [f"{cid}.{trigger}" for cid, probes in bank.open_probes.items()
               for trigger, text in probes.items() if banks.SUBJECT_TOKEN not in text]
    missing += [q["id"] for q in bank.qualitative_fixed
                if banks.SUBJECT_TOKEN not in q["prompt"]]
    assert not missing, f"questions that do not name the subject: {missing}"


def test_the_self_form_never_names_the_subject_in_the_third_person():
    """Reading "does Ana…" about yourself is the rater wording leaking over."""
    item = banks.Item(id="X", label="x", prompt="Does {subject} deliver?", competency_id="C1")
    assert item.prompt_for(banks.SELF, "Ana") == "Does this person deliver?"


# --------------------------------------------------------------- the interview

def _run(driver, likert, open_answer):
    def answer(pending, _turn):
        kind = pending["kind"]
        if kind in ("consent", "review"):
            return "yes"
        if kind == "likert":
            return likert
        return open_answer
    d = driver(answer)
    final = d.run(start())
    assert final["stage"] == STAGE_DONE
    return d.asked


@pytest.mark.parametrize("likert,open_answer", [
    ("1", GOOD),              # low probes
    ("3", GOOD),              # high probes
    ("not observed", THIN),   # not-observed probes, then follow-ups on thin answers
    ("2", THIN),              # default probes and closing-question follow-ups
])
def test_no_question_reaches_the_rater_with_the_placeholder(driver, isolated_db,
                                                              likert, open_answer):
    asked = _run(driver, likert, open_answer)
    leaked = [p["text"] for p in asked if "{subject}" in p.get("text", "")
              or "{competency}" in p.get("text", "")]
    assert not leaked


def test_rating_and_closing_questions_use_the_first_name_only(driver, isolated_db):
    asked = _run(driver, "2", GOOD)
    likert = [p["text"] for p in asked if p["kind"] == "likert"]
    closing = [p["text"] for p in asked if p["kind"] == "open"
               and p.get("theme") and not p.get("is_followup")]
    assert likert and closing
    for text in likert + closing:
        assert "Ana" in text, text
        assert "Ana Torres" not in text, "the questions use the first name"


def test_a_missing_display_name_reads_this_person(driver, isolated_db):
    """A checkpoint from before the name was carried must still render a question."""
    def answer(pending, _turn):
        return "yes" if pending["kind"] == "consent" else StopIteration
    d = driver(answer)
    d.run({**start(), "subject_display": ""})
    first = next(p for p in d.asked if p["kind"] == "likert")
    assert first["text"].startswith("Does this person ")


# ------------------------------------------------------------------ the model

def test_the_model_is_given_the_placeholder_not_the_name():
    """Filling after the model call keeps the name out of it."""
    seen = []

    class _Capture:
        def invoke(self, prompt):
            seen.append(prompt)
            return type("R", (), {"content": "How does {subject} decide?"})()

    client = llm.OpenAILLM.__new__(llm.OpenAILLM)
    client._llm, client._model, client._fallback = _Capture(), "m", llm.StubLLM()
    out = client.probe("How does {subject} handle {competency}?", "Decisions")
    assert "{subject}" in seen[0] and "Ana" not in seen[0]
    assert fill_subject(out, "Ana") == "How does Ana decide?"
