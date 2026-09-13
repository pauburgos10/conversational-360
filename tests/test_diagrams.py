"""The diagrams are derived, so they get tested like code.

The load-bearing test here is the classification one: adding a table to the
response store forces an explicit decision about which side of the
confidentiality boundary it sits on. In a project whose central claim is that
identity is separated structurally, a new table appearing unclassified is
exactly the change that should stop the build.
"""

import re
import tempfile
from pathlib import Path

import pytest

from poc import cli_diagram, store


@pytest.fixture
def schema_tables(tmp_path):
    path = tmp_path / "schema.db"
    store.init(path)
    return set(store.schema_columns(path))


def test_every_table_is_classified(schema_tables):
    unclassified = schema_tables - set(cli_diagram.BOUNDARY)
    assert not unclassified, (
        f"tables missing from BOUNDARY in poc/cli_diagram.py: {sorted(unclassified)}. "
        "Decide which side of the confidentiality boundary each one sits on."
    )


def test_no_stale_classifications(schema_tables):
    stale = set(cli_diagram.BOUNDARY) - schema_tables
    assert not stale, f"BOUNDARY lists tables that no longer exist: {sorted(stale)}"


def test_erd_covers_every_table_and_column(schema_tables, tmp_path):
    erd = cli_diagram.erd_markdown()
    path = tmp_path / "s.db"
    store.init(path)
    for table, columns in store.schema_columns(path).items():
        assert table in erd
        for column in columns:
            assert column in erd, f"{table}.{column} missing from the ERD"


def test_erd_states_where_identity_lives_and_who_resolves_it():
    """The ERD is a deliverable, so its narrative has to track the design.

    It used to claim nothing could reconnect a response to a reviewer. HR now
    can, deliberately, and a diagram still asserting the old guarantee would be
    the most misleading document in the repo.
    """
    erd = cli_diagram.erd_markdown()
    assert "No table above carries a reviewer's name" in erd
    assert "pseudonymous is not anonymous" in erd
    assert "identity.db" in erd
    assert "rater_labels()" in erd, "the ERD should name the one function that attributes"


def test_erd_says_the_store_keeps_text_as_written():
    """The masking move is a schema-visible fact and belongs on the data model."""
    erd = cli_diagram.erd_markdown()
    assert "as written" in erd
    assert "AUDIENCE_SUBJECT" in erd


def test_erd_marks_foreign_keys():
    erd = cli_diagram.erd_markdown()
    assert "subject ||--o{ ballot : has" in erd
    assert "TEXT subject_id FK" in erd


def test_graph_diagram_matches_the_compiled_app():
    doc = cli_diagram.graph_markdown()
    for node in ("next_question", "collect", "finish"):
        assert node in doc
    assert "```mermaid" in doc
    # the conditional edges are what make the loop work
    assert "-.->" in doc


def test_graph_diagram_needs_no_network(monkeypatch):
    """draw_mermaid is text; draw_mermaid_png would need Chromium or mermaid.ink."""
    import socket

    def guard(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    assert "```mermaid" in cli_diagram.graph_markdown()


def test_generated_files_are_marked_as_generated():
    for text in (cli_diagram.graph_markdown(), cli_diagram.erd_markdown()):
        assert text.startswith(cli_diagram.BANNER)
        assert "Do not edit by hand" in text


def test_main_writes_both_files(monkeypatch, tmp_path):
    from poc import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    cli_diagram.main()
    assert (tmp_path / "docs" / "graph.md").exists()
    assert (tmp_path / "docs" / "erd.md").exists()
