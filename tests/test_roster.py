"""The subject roster is the one input an administrator must edit, so it fails
loudly and specifically rather than producing a silently wrong round."""

import pytest

from poc import roster


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
    assert by_id["S1"].role == "Talent Partner"
    assert by_id["S1"].cohort == "talent"
    assert all(s.display_name and s.cohort for s in subjects)


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
