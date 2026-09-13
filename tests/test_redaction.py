"""Redaction runs before persistence, so these are the tests that decide
whether an identifying detail can reach disk at all."""

import pytest

from poc.graph.redaction import (
    CODE_EMAIL, CODE_NAME, CODE_PHONE, CODE_ROLE, CODE_SELF_ID, CODE_TICKET, CODE_URL,
    Redactor, redact,
)

GAZ = {"Ana Torres", "Marcos Vidal"}


@pytest.mark.parametrize("text,code", [
    ("write to ana.torres@pluralit.com about it", CODE_EMAIL),
    ("call me on +34 611 222 333 tomorrow", CODE_PHONE),
    ("see https://notion.so/team/page for detail", CODE_URL),
    ("it was tracked in FEAT-4412 last month", CODE_TICKET),
    ("as their direct report I saw this often", CODE_ROLE),
    ("as their only direct report I saw this", CODE_SELF_ID),
    ("Ana Torres handled the escalation", CODE_NAME),
])
def test_each_rule_fires(text, code):
    assert code in redact(text, GAZ).codes


def test_clean_feedback_is_left_alone():
    text = ("They delivered the migration on time and documented the runbook, "
            "so the team could run it without them the following month.")
    result = redact(text, GAZ)
    assert result.clean
    assert result.text == text


def test_relationship_disclosure_is_removed():
    """Round 1 forbids any breakdown by role, so free text disclosing the
    rater's relationship is a re-identification vector, not just noise."""
    result = redact("I report to them and they are consistently clear", GAZ)
    assert CODE_ROLE in result.codes
    assert "report to them" not in result.text


def test_self_identifying_phrase_stops_at_the_clause_boundary():
    """Over-redaction is the safe direction, but swallowing whole sentences
    would destroy the feedback's usefulness."""
    result = redact("as their only direct report I still learned a lot from them", GAZ)
    assert CODE_SELF_ID in result.codes
    assert "learned a lot" in result.text


def test_findings_record_length_never_content():
    result = redact("mail me at ana.torres@pluralit.com", GAZ)
    finding = next(f for f in result.findings if f.code == CODE_EMAIL)
    assert finding.length == len("ana.torres@pluralit.com")
    assert not hasattr(finding, "text")
    assert "ana.torres" not in str(finding)


def test_sentence_initial_capitals_are_not_treated_as_names():
    text = "Delivery slipped twice. Quality Assurance caught it both times."
    result = redact(text, set())
    assert "Delivery" in result.text


def test_org_vocabulary_is_allowlisted():
    result = redact("Their work across Legal Immigration and Business Process was steady", set())
    assert CODE_NAME not in result.codes


def test_gazetteer_prefers_the_longest_match():
    result = redact("Ana Torres reviewed it", {"Ana", "Ana Torres"})
    assert result.text.count("[NAME]") == 1


def test_redactor_reads_a_gazetteer_file(tmp_path):
    path = tmp_path / "names.txt"
    path.write_text("# comment\nElena Ruiz\n", encoding="utf-8")
    assert CODE_NAME in Redactor.from_file(path).apply("Elena Ruiz was clear").codes


def test_empty_input_is_safe():
    assert redact("", GAZ).text == ""
    assert redact(None, GAZ).findings == []


# ---------------------------------------------------------------- name casing

@pytest.mark.parametrize("text", [
    "Ana Torres handled the escalation",
    "ana torres handled the escalation",
    "ANA TORRES handled the escalation",
    "Ana torres handled the escalation",
])
def test_an_explicit_full_name_is_caught_whatever_the_casing(text):
    """Raters type into a chat box, not a form. Casual capitalisation is normal
    and must not be the difference between redacted and leaked."""
    result = redact(text, GAZ)
    assert CODE_NAME in result.codes
    assert "orres" not in result.text


def test_a_full_name_collapses_to_a_single_marker():
    assert redact("Ana Torres handled it", GAZ).text.count("[NAME]") == 1


def test_a_capitalised_surname_alone_is_caught():
    r = Redactor(gazetteer={"Ana Torres"}, derived={"Ana", "Torres"})
    assert CODE_NAME in r.apply("Torres was clear about the risk").codes


def test_a_lowercase_derived_token_is_left_alone():
    """The deliberate trade-off. Half the surnames on a Spanish-speaking roster
    are also ordinary words, so an inferred fragment needs its capital to count."""
    r = Redactor(gazetteer={"Ana Torres"}, derived={"Ana", "Torres"})
    assert r.apply("las torres del edificio").clean


def test_spanish_prose_survives_a_realistic_roster():
    from poc import config
    r = Redactor.from_file(config.GAZETTEER_PATH)
    text = "Cambió los marcos del proceso y dejó las salas en blanco; tomas notas de las torres."
    result = r.apply(text)
    assert result.clean, f"false positives on ordinary Spanish: {result.text}"


def test_name_particles_are_not_derived():
    """'de'/'la' as standalone rules would match constantly."""
    from poc.graph.redaction import _derive
    assert _derive("Ana de la Cruz") == {"Ana", "Cruz"}


def test_derived_tokens_skip_the_org_allowlist():
    from poc.graph.redaction import _derive
    assert "Legal" not in _derive("Legal Immigration")


def test_a_single_token_entry_the_admin_wrote_is_matched_case_insensitively():
    """What an administrator types is honoured as written; only inferred
    fragments are treated conservatively."""
    r = Redactor(gazetteer={"Okonkwo"})
    assert CODE_NAME in r.apply("okonkwo raised it in the retro").codes


def test_derived_never_shadows_an_explicit_entry():
    r = Redactor(gazetteer={"Torres"}, derived={"Torres"})
    assert r.derived == set()
    assert CODE_NAME in r.apply("torres flagged the risk").codes


def test_from_file_splits_entries_into_two_tiers():
    from poc import config
    r = Redactor.from_file(config.GAZETTEER_PATH)
    assert "Ana Torres" in r.gazetteer
    assert "Torres" in r.derived
    assert not r.gazetteer & r.derived
