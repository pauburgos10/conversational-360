"""The roster must persist wherever the database persists.

On a Render paid instance the service gets one persistent disk, mounted at
/app/data. Everything the app writes lives there — except the roster, which
ships in inputs/ and is appended to by `roster.add_subject`.

Leave it there and a restart produces a quiet confidentiality failure: the
subject added through /admin is still in `responses.db` with their ballots, but
the roster reverts to the bundled copy, `redaction_names()` no longer lists
them, and their name appears unmasked in the subject's copy of the report.
`C360_ROSTER_IN_DATA` is the fix; these tests pin both the failure and the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poc import config, roster

HEADER = "subject_id,display_name,role,cohort\n"


@pytest.fixture
def disk(tmp_path):
    """A bundled inputs/ roster and an empty persistent data/ directory."""
    inputs = tmp_path / "inputs"
    data = tmp_path / "data"
    inputs.mkdir()
    data.mkdir()
    bundled = inputs / "subjects.csv"
    bundled.write_text(HEADER + "S1,Ana Torres,Talent Partner,talent\n", encoding="utf-8")
    yield data, bundled
    roster.load_subjects.cache_clear()


def _names(path: Path) -> set[str]:
    roster.load_subjects.cache_clear()
    explicit, _ = roster.redaction_names(path)
    return explicit


def test_off_by_default_the_bundled_roster_is_used(monkeypatch, disk):
    data, bundled = disk
    monkeypatch.delenv("C360_ROSTER_IN_DATA", raising=False)
    assert config.roster_path(data, bundled) == bundled
    assert not (data / "subjects.csv").exists(), "nothing should be copied when off"


def test_on_the_first_boot_copies_the_bundled_roster_onto_the_disk(monkeypatch, disk):
    data, bundled = disk
    monkeypatch.setenv("C360_ROSTER_IN_DATA", "1")
    live = config.roster_path(data, bundled)
    assert live == data / "subjects.csv"
    assert live.read_text(encoding="utf-8") == bundled.read_text(encoding="utf-8")


def test_a_restart_does_not_overwrite_subjects_added_at_runtime(monkeypatch, disk):
    """The property that closes the leak: the disk copy wins on every later boot."""
    data, bundled = disk
    monkeypatch.setenv("C360_ROSTER_IN_DATA", "1")

    live = config.roster_path(data, bundled)                 # first boot
    roster.add_subject("Sofia Marin", path=live)             # HR adds someone

    after_restart = config.roster_path(data, bundled)        # next boot
    assert after_restart == live
    assert "Sofia Marin" in after_restart.read_text(encoding="utf-8")


def test_without_the_fix_a_restart_drops_the_name_from_the_mask(monkeypatch, disk):
    """The failure itself, reproduced — so the fix below is known to address it.

    Off: the roster the running app writes to is the bundled file in inputs/,
    which the container image restores on restart. Modelled here by re-creating
    the bundled file from the image's original contents.
    """
    data, bundled = disk
    monkeypatch.delenv("C360_ROSTER_IN_DATA", raising=False)
    original = bundled.read_text(encoding="utf-8")

    path = config.roster_path(data, bundled)
    roster.add_subject("Sofia Marin", path=path)
    assert "Sofia Marin" in _names(path), "precondition: masked while running"

    bundled.write_text(original, encoding="utf-8")           # restart restores the image
    assert "Sofia Marin" not in _names(config.roster_path(data, bundled)), \
        "without the fix, the name silently leaves the mask"


def test_with_the_fix_the_name_stays_masked_across_a_restart(monkeypatch, disk):
    data, bundled = disk
    monkeypatch.setenv("C360_ROSTER_IN_DATA", "1")
    original = bundled.read_text(encoding="utf-8")

    path = config.roster_path(data, bundled)
    roster.add_subject("Sofia Marin", path=path)

    bundled.write_text(original, encoding="utf-8")           # restart restores the image
    assert "Sofia Marin" in _names(config.roster_path(data, bundled)), \
        "the live roster is on the disk, so the name must survive"


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_the_switch_accepts_the_usual_truthy_values(monkeypatch, disk, value):
    data, bundled = disk
    monkeypatch.setenv("C360_ROSTER_IN_DATA", value)
    assert config.roster_path(data, bundled) == data / "subjects.csv"
