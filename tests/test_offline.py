"""Proof that the engine runs with no API key and touches no network.

The README claims this. Claiming it is not the same as enforcing it: on a
machine with OPENAI_API_KEY exported, `get_llm()` would resolve to the live
client and the graph would start making real calls. So rather than trusting the
resolution logic, these tests sever the socket layer outright and run the whole
pipeline through it. If any code path reaches for the network, they fail.
"""

import socket

import pytest

from poc import config
from poc.llm import OpenAILLM, StubLLM, get_llm


@pytest.fixture
def severed_network(monkeypatch):
    """Make any outbound connection raise, not hang."""

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted — this must run offline")

    monkeypatch.setattr(socket, "socket", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)


# ------------------------------------------------------------------ resolution

def test_absent_key_resolves_to_the_stub(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("C360_LLM_MODE", "auto")
    assert config.llm_settings().mode == "stub"
    assert isinstance(get_llm(), StubLLM)


def test_a_present_key_would_resolve_to_the_live_client(monkeypatch):
    """The counterpart: stub mode is a consequence of configuration, not a
    hardcoded fallback that would silently ignore a real key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("C360_LLM_MODE", "auto")
    assert config.llm_settings().mode == "openai"
    assert isinstance(get_llm(), OpenAILLM)


def test_explicit_stub_mode_overrides_a_present_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("C360_LLM_MODE", "stub")
    assert isinstance(get_llm(), StubLLM)


def test_openai_mode_without_a_key_degrades_rather_than_crashing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("C360_LLM_MODE", "openai")
    assert config.llm_settings().mode == "stub"


# --------------------------------------------------------------- offline proof

def test_a_full_interview_completes_with_the_network_severed(
    severed_network, driver, tmp_path, monkeypatch
):
    from poc import store
    from poc.banks import load_bank

    path = tmp_path / "responses.db"
    monkeypatch.setattr(config, "RESPONSES_DB", path)
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)

    good = ("Last quarter they caught the vendor delay in week 2 and re-sequenced "
            "the plan, which meant we still shipped on time.")

    def answer(pending, _turn):
        if pending["kind"] in ("consent", "review"):
            return "yes"
        return "2" if pending["kind"] == "likert" else good

    final = driver(answer).run({
        "ballot_id": "offline", "subject_id": "S1", "subject_display": "Ana Torres",
        "round_id": "R1", "stage": "consent",
    })
    assert final["submitted"] is True
    assert len(final["likert"]) == len(load_bank().all_items)


def test_the_aggregation_pipeline_runs_with_the_network_severed(
    severed_network, tmp_path, monkeypatch, rules
):
    from poc import reporting, store
    from poc.aggregation import rollup, suppression

    path = tmp_path / "responses.db"
    monkeypatch.setattr(config, "RESPONSES_DB", path)
    store.init(path)
    store.upsert_subject("S1", "Ana Torres", "talent", "Talent Partner", path=path)
    for i in range(6):
        ballot = f"b{i}"
        store.create_ballot("S1", "R1", ballot_id=ballot, path=path)
        store.save_submission(
            ballot,
            {"C1.1": 2, "C1.2": 3, "C1.3": 2},
            [{"ref": "Q1", "text": "they documented the runbook and handed it over", "codes": []}],
            [], path=path,
        )

    data = store.fetch_round("R1", path=path)
    cells = rollup.build_cells(data)
    marks = suppression.evaluate(cells, rules)
    report = reporting.individual_report("S1", data, cells, marks, rules)
    assert "Manager-Ready Development Report" in report
    assert reporting.aggregates_csv(cells, marks, rules).count("\n") > 0


def test_langgraph_itself_never_calls_a_model(severed_network):
    """Orchestration, checkpointing and interrupt/resume are pure infrastructure.
    The only model touchpoints in this POC live in poc/llm.py."""
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command

    from poc.graph.build import build_graph

    app = build_graph(SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    cfg = {"configurable": {"thread_id": "no-net"}}
    out = app.invoke({"ballot_id": "x", "subject_id": "S1", "round_id": "R1",
                      "stage": "consent"}, cfg)
    assert out["__interrupt__"][0].value["kind"] == "consent"
    out = app.invoke(Command(resume="yes"), cfg)
    assert out["__interrupt__"][0].value["kind"] == "likert"
