"""Guardrails implement two hard business rules from the brief."""

import pytest

from poc.graph import guardrails as g


@pytest.mark.parametrize("text", [
    "honestly I can't go on like this",
    "some days I want to hurt myself",
    "there is no reason to live",
])
def test_crisis_is_detected_and_halts(text):
    verdict = g.check(text)
    assert verdict.kind == g.KIND_CRISIS
    assert verdict.halts
    assert verdict.blocks_recording
    assert g.EAP_PLACEHOLDER in verdict.message


def test_crisis_message_signposts_and_does_not_advise():
    message = g.check("I want to kill myself").message
    assert g.EAP_PLACEHOLDER in message
    assert "saved" in message.lower()
    for word in ("you should", "try to", "have you considered"):
        assert word not in message.lower(), "the assistant must signpost, not counsel"


@pytest.mark.parametrize("text,code", [
    ("should I fire them?", "HR_DECISION"),
    ("do they deserve a promotion", "HR_DECISION"),
    ("what salary should they be on", "HR_DECISION"),
    ("am I depressed?", "CLINICAL_ADVICE"),
    ("can you counsel me through this", "CLINICAL_ADVICE"),
    ("their divorce is affecting the team", "PRIVATE_LIFE"),
    ("they are better than Marcos at this", "COMPARE_NAMED"),
])
def test_refusals(text, code):
    verdict = g.check(text)
    assert verdict.kind == g.KIND_REFUSE
    assert verdict.code == code
    assert verdict.blocks_recording


def test_hr_refusal_states_the_boundary_rather_than_deflecting():
    message = g.check("should we promote them?").message.lower()
    assert "person" in message or "people" in message


@pytest.mark.parametrize("text", [
    "they meet every deadline and flag risk early",
    "I would like to see them delegate more of the reporting",
    "the handover documentation was thorough",
])
def test_ordinary_feedback_passes_untouched(text):
    assert g.check(text).kind == g.KIND_OK


def test_overwhelmed_alone_is_not_a_crisis():
    """Firing on ordinary workplace language would train raters to self-censor."""
    assert g.check("the team is overwhelmed by the volume of reqs").kind == g.KIND_OK


def test_distress_supports_without_halting():
    verdict = g.check("I am completely overwhelmed at the moment")
    assert verdict.kind == g.KIND_DISTRESS
    assert not verdict.halts
    assert not verdict.blocks_recording
    assert g.EAP_PLACEHOLDER in verdict.message


def test_crisis_outranks_a_refusal_in_the_same_turn():
    verdict = g.check("should I fire them? honestly I can't go on")
    assert verdict.kind == g.KIND_CRISIS


def test_empty_input_is_ok():
    assert g.check("").kind == g.KIND_OK
    assert g.check(None).kind == g.KIND_OK
