"""The subject roster is the one input an administrator must edit, so it fails
loudly and specifically rather than producing a silently wrong round."""

import pytest

from poc import config, roster


def write(tmp_path, text):
    path = tmp_path / "subjects.csv"
    path.write_text(text, encoding="utf-8")
    roster.load_subjects.cache_clear()
    return path


def test_the_shipped_roster_loads(tmp_path):
    """The six seeded subjects parse, and ids stay unique.

    Deliberately not `len(subjects) == 6`. The roster is editable from /admin now,
    so an operator adding a real subject would fail a count assertion that was
    only ever standing in for "the file parses". What matters is that the seeded
    rows are intact and that nothing has collided with them — an added subject is
    the feature working, a duplicate id is a bug.
    """
    roster.load_subjects.cache_clear()
    subjects = roster.load_subjects()

    by_id = {s.subject_id: s for s in subjects}
    assert len(by_id) == len(subjects), "duplicate subject_id in the roster"
    assert {"S1", "S2", "S3", "S4", "S5", "S6"} <= set(by_id)

    assert by_id["S1"].display_name == "Ana Torres"
    assert by_id["S1"].role == "Talent Partner Lead"
    assert by_id["S1"].cohort == "Talent"
    assert all(s.display_name and s.cohort for s in subjects)


def test_the_shipped_organisation_is_one_tree():
    """The synthetic org is what HR browses and mints from, so it must hold
    together: one person at the top, every manager on the list, no loops, and
    everyone reachable from the top.

    A broken line does not fail the load — `load_subjects` drops it rather than
    lose the redaction list — so this is where a bad edit to the shipped file is
    caught.
    """
    import csv

    with config.SUBJECTS_PATH.open(encoding="utf-8", newline="") as fh:
        raw = {r["subject_id"]: (r.get("manager_id") or "").strip() for r in csv.DictReader(fh)}
    assert all(m in raw for m in raw.values() if m), "a manager_id names nobody on the list"

    tops = [sid for sid, manager in raw.items() if not manager]
    assert len(tops) == 1, f"expected one person at the top, got {tops}"

    for sid in raw:
        seen, current = set(), sid
        while raw[current]:
            assert current not in seen, f"reporting loop through {sid}"
            seen.add(current)
            current = raw[current]
        assert current == tops[0], f"{sid} does not report up to the top"


def test_every_shipped_role_is_a_job_title():
    """Departments belong in `cohort`; a role column reading "Accounting" was the
    sign it had been used for both."""
    for subject in roster.load_subjects():
        assert subject.role, f"{subject.subject_id} has no role"
        assert subject.role.casefold() != subject.cohort.casefold(), subject.subject_id


def test_roster_job_titles_are_masked_when_a_reviewer_claims_one(tmp_path):
    """The fixed rules knew only talent titles; any title on the roster now counts."""
    from poc.graph.redaction import Redactor

    path = write(tmp_path, "subject_id,display_name,role,cohort\n"
                           "S1,Ana Torres,Senior Business Process Analyst,Operations\n")
    masker = Redactor(*roster.redaction_names(path), roles=roster.redaction_roles(path))
    for text in ("As the only business process analyst I saw every handover.",
                 "As a senior business process analyst, I review their work."):
        result = masker.apply(text)
        assert "ROLE" in result.codes or "SELF_ID" in result.codes, text
        assert "analyst" not in result.text.lower(), text
    assert masker.apply("They improved the business process.").clean


def test_a_missing_file_names_the_columns_it_wants(tmp_path):
    roster.load_subjects.cache_clear()
    with pytest.raises(FileNotFoundError, match="subject_id"):
        roster.load_subjects(tmp_path / "absent.csv")


def test_a_missing_column_is_named(tmp_path):
    path = write(tmp_path, "subject_id,display_name,cohort\nS1,Ana Torres,talent\n")
    with pytest.raises(ValueError, match="role"):
        roster.load_subjects(path)


def test_a_duplicate_id_is_rejected_with_its_line_number(tmp_path):
    path = write(tmp_path, "subject_id,display_name,role,cohort\n"
                           "S1,Ana Torres,Talent Partner,talent\n"
                           "S1,Marcos Vidal,Sourcer,talent\n")
    with pytest.raises(ValueError, match="line 3"):
        roster.load_subjects(path)


def test_an_empty_name_is_rejected(tmp_path):
    path = write(tmp_path, "subject_id,display_name,role,cohort\nS1,,Talent Partner,talent\n")
    with pytest.raises(ValueError, match="required"):
        roster.load_subjects(path)


def test_a_header_with_no_rows_is_rejected(tmp_path):
    path = write(tmp_path, "subject_id,display_name,role,cohort\n")
    with pytest.raises(ValueError, match="no subjects"):
        roster.load_subjects(path)


def test_a_blank_cohort_becomes_unassigned(tmp_path):
    path = write(tmp_path, "subject_id,display_name,role,cohort\nS1,Ana Torres,Talent Partner,\n")
    assert roster.load_subjects(path)[0].cohort == "unassigned"


def test_subject_names_are_merged_into_the_redaction_list():
    """A roster entry missing from the gazetteer would otherwise leak that name."""
    roster.load_subjects.cache_clear()
    explicit, _derived = roster.redaction_names()
    for subject in roster.load_subjects():
        assert subject.display_name in explicit


def test_redaction_still_works_without_a_roster(tmp_path):
    """Redaction must not depend on a file the administrator has not written yet."""
    roster.load_subjects.cache_clear()
    explicit, derived = roster.redaction_names(tmp_path / "absent.csv")
    assert explicit and not explicit & derived
