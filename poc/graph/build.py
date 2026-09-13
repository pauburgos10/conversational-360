"""Graph assembly.

    START ─▶ next_question ─┬─(pending)──▶ collect ─┬─▶ next_question   (loop)
                            │                        └─▶ finish
                            └─(nothing left)────────────▶ finish ─▶ END

Checkpointer is SqliteSaver keyed on `thread_id = ballot_id`. That single
choice delivers the brief's "save-and-resume: Required Y" without a session
table, a login, or any server-side notion of who the rater is.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import config
from . import nodes
from .state import InterviewState


def _route_after_question(state: InterviewState) -> str:
    return "collect" if state.get("pending") else "finish"


def build_graph(checkpointer):
    g = StateGraph(InterviewState)
    g.add_node("next_question", nodes.next_question)
    g.add_node("collect", nodes.make_collect(interrupt))
    g.add_node("finish", nodes.finish)

    g.add_edge(START, "next_question")
    g.add_conditional_edges("next_question", _route_after_question,
                            {"collect": "collect", "finish": "finish"})
    g.add_conditional_edges("collect", nodes.route_after_collect,
                            {"next_question": "next_question", "finish": "finish"})
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def sqlite_checkpointer(path: Path | None = None) -> SqliteSaver:
    conn = sqlite3.connect(path or config.CHECKPOINTS_DB, check_same_thread=False)
    return SqliteSaver(conn)


def build_app(path: Path | None = None):
    return build_graph(sqlite_checkpointer(path))
