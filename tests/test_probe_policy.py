"""The probe policy panel: HR tunes four settings, and they genuinely take effect.

Two failure modes these tests exist for. A control that saves but changes
nothing is worse than no control — HR would tune the interview and see no
difference. And an explanation that misdescribes the engine is worse than no
explanation, so the claims the panel makes are asserted here too.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from poc import banks, llm, policy
from poc.graph import nodes
from tests.conftest import sign_in_as_hr

GOOD = {"max_probed_competencies": 4, "max_followups_per_probe": 2,
        "max_followups_per_session": 8, "min_words": 8}


@pytest.fixture
def hr():
    return sign_in_as_hr(TestClient(app))


# --------------------------------------------------------------- the settings

def test_only_settings_the_engine_reads_are_editable():
    """`trigger_priority` is in questions.json but no code consults it."""
    assert set(policy.FIELD_KEYS) == {"max_probed_competencies", "max_followups_per_probe",
                                      "max_followups_per_session", "min_words"}
    assert "trigger_priority" not in policy.FIELD_KEYS


def test_every_editable_setting_is_actually_read_by_the_engine():
    """Guards against a future field being added to the panel but wired to nothing."""
    import inspect
    from poc.graph import nodes as n
    source = inspect.getsource(n) + inspect.getsource(llm)
    for key in policy.FIELD_KEYS:
        assert f'"{key}"' in source, f"{key} is editable but nothing reads it"


def test_defaults_come_from_questions_json():
    shipped = json.loads(open(policy.config.QUESTIONS_PATH).read())["probe_policy"]
    assert policy.bundled_defaults() == {k: shipped[k] for k in policy.FIELD_KEYS}


def test_an_edit_takes_effect_without_a_restart():
    """The whole point of `cache_clear`: new value, same process."""
    assert llm.min_words() == policy.bundled_defaults()["min_words"]
    policy.save_overrides({**GOOD, "min_words": 30})
    assert banks.load_bank().probe_policy["min_words"] == 30
    assert llm.min_words() == 30


def test_the_word_floor_edit_changes_how_answers_are_judged():
    policy.save_overrides({**GOOD, "min_words": 30})
    ten_words = "They rebuilt the intake checklist and ramp time dropped fast."
    assert llm.heuristic_specificity(ten_words).gap == llm.GAP_TOO_SHORT


def test_the_probe_budget_edit_changes_the_plan():
    all_low = {i.id: 1 for i in banks.load_bank().all_items}
    assert nodes.plan_probes(all_low, banks.load_bank())          # default budget: some

    policy.save_overrides({**GOOD, "max_probed_competencies": 0})
    assert nodes.plan_probes(all_low, banks.load_bank()) == []


def test_edits_never_rewrite_questions_json():
    """That file holds HR- and Legal-reviewed wording; a web request must not touch it."""
    before = policy.config.QUESTIONS_PATH.read_bytes()
    policy.save_overrides({**GOOD, "min_words": 20})
    policy.reset_overrides()
    assert policy.config.QUESTIONS_PATH.read_bytes() == before


def test_overrides_are_stored_under_data():
    """So they persist exactly as long as everything else there does."""
    assert policy.OVERRIDES_PATH.name == "probe_policy.json"


def test_reset_restores_the_shipped_defaults():
    policy.save_overrides({**GOOD, "min_words": 40})
    policy.reset_overrides()
    assert banks.load_bank().probe_policy["min_words"] == policy.bundled_defaults()["min_words"]


@pytest.mark.parametrize("bad", ['{"min_words": 999}', '{"min_words": "30"}',
                                 '{"min_words": true}', "not json", "[1,2]"])
def test_a_corrupt_overrides_file_falls_back_rather_than_crashing(bad):
    policy.OVERRIDES_PATH.write_text(bad, encoding="utf-8")
    banks.load_bank.cache_clear()
    assert banks.load_bank().probe_policy["min_words"] == policy.bundled_defaults()["min_words"]


@pytest.mark.parametrize("field,value", [
    ("min_words", "0"), ("min_words", "61"), ("max_probed_competencies", "11"),
    ("max_followups_per_probe", "-1"), ("min_words", "eight"), ("min_words", "2.5"),
])
def test_out_of_bounds_values_are_refused(field, value):
    with pytest.raises(ValueError):
        policy.validate({**{k: str(v) for k, v in GOOD.items()}, field: value})


# --------------------------------------------------------------- the panel

def test_the_suppression_card_is_gone(hr):
    page = hr.get("/admin").text
    assert ">Suppression<" not in page
    assert "cells evaluated" not in page


def test_the_panel_shows_each_setting_with_its_explanation(hr):
    page = hr.get("/admin").text
    assert "How written questions are chosen" in page
    for f in policy.FIELDS:
        assert f.label in page
        assert f'name="{f.key}"' in page


def test_saving_over_http_persists_and_redisplays(hr):
    r = hr.post("/admin/policy", data={**GOOD, "min_words": 15}, follow_redirects=True)
    assert "Settings saved" in r.text
    assert re.search(r'name="min_words"\s+value="15"', r.text)
    assert banks.load_bank().probe_policy["min_words"] == 15


def test_a_bad_value_over_http_is_reported_and_not_saved(hr):
    r = hr.post("/admin/policy", data={**GOOD, "min_words": 500}, follow_redirects=True)
    assert "Not saved" in r.text
    assert not policy.OVERRIDES_PATH.exists()


def test_the_panel_is_hr_only():
    anon = TestClient(app)
    assert anon.post("/admin/policy", data=GOOD, follow_redirects=False).status_code in (303, 401, 403)
    assert not policy.OVERRIDES_PATH.exists(), "a rater must not be able to change the policy"


def test_the_rules_shown_to_hr_come_from_the_engine(hr, monkeypatch):
    """A threshold typed into the template would drift. Change the constant, the page follows."""
    monkeypatch.setattr(nodes, "HIGH_MEAN_MIN", 2.9)
    assert "2.9 or above" in hr.get("/admin").text


def test_the_panel_does_not_claim_zero_turns_off_all_written_questions():
    """The closing questions are asked regardless — HR must not be told otherwise."""
    field = next(f for f in policy.FIELDS if f.key == "max_probed_competencies")
    n = len(banks.load_bank().qualitative_fixed)
    help_text = policy.rendered_help(field, n)
    assert f"the {n} closing questions are still asked" in help_text


@pytest.mark.parametrize("n,expected", [
    (4, "the 4 closing questions are still asked"),
    (5, "the 5 closing questions are still asked"),
    (1, "the closing question is still asked"),
])
def test_the_closing_question_count_follows_the_bank(n, expected):
    """Typed as "four", this would go stale the day a fifth closing question was added."""
    field = next(f for f in policy.FIELDS if f.key == "max_probed_competencies")
    assert expected in policy.rendered_help(field, n)


def test_no_help_text_is_left_with_an_unfilled_placeholder(hr):
    page = hr.get("/admin").text
    assert "{closing" not in page


# --------------------------------------------------------------- the "12" in llm.py

def test_the_judge_reason_length_is_independent_of_min_words():
    """They once shared the literal 12. Changing the answer floor must not move it."""
    before = llm.JUDGE_REASON_MAX_WORDS
    policy.save_overrides({**GOOD, "min_words": 40})
    assert llm.JUDGE_REASON_MAX_WORDS == before
    assert llm.min_words() == 40
