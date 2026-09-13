import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from poc.graph.build import build_graph


@pytest.fixture(autouse=True)
def force_stub_mode(monkeypatch):
    """Pin every test to the offline client, whatever the machine has set.

    Without this the suite inherits OPENAI_API_KEY from the developer's shell and
    quietly starts making live calls — slow, non-deterministic, billable, and it
    would invalidate every assertion that depends on the stub's fixed wording.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("C360_LLM_MODE", "stub")


@pytest.fixture
def rules():
    return {
        "round": {"id": "T", "label": "test round"},
        "suppression": {
            "min_n": 4,
            "dominance_max_share": 0.5,
            "complementary": True,
            "codes": {
                "MIN_N": "Suppressed: fewer than {min_n} contributing responses",
                "DOMINANCE": "Suppressed: one response accounts for more than {pct}%",
                "COMPLEMENTARY": "Suppressed: publishing this value would reveal a suppressed sibling",
            },
        },
        "themes": {"min_ballots_per_theme": 3, "allow_verbatim_quotes": False, "max_themes_per_report": 6},
        "bands": [
            {"id": "developing", "max_exclusive": 2.0, "label": "In development"},
            {"id": "meets", "max_inclusive": 2.5, "label": "Meets expectations"},
            {"id": "reference", "label": "Reference"},
        ],
    }


class Driver:
    """Drives the real graph with a scripted answering function."""

    def __init__(self, answer_fn, thread_id="t"):
        self.app = build_graph(InMemorySaver())
        self.cfg = {"configurable": {"thread_id": thread_id}}
        self.answer_fn = answer_fn
        self.asked = []
        self.notices = []

    def run(self, initial, max_turns=120):
        out = self.app.invoke(initial, self.cfg)
        turns = 0
        while out.get("__interrupt__"):
            pending = out["__interrupt__"][0].value
            self.asked.append(pending)
            reply = self.answer_fn(pending, turns)
            if reply is StopIteration:
                break
            out = self.app.invoke(Command(resume=reply), self.cfg)
            if out.get("notice"):
                self.notices.append(out["notice"])
            turns += 1
            assert turns <= max_turns, "conversation did not terminate"
        return self.app.get_state(self.cfg).values


@pytest.fixture
def driver():
    return Driver
