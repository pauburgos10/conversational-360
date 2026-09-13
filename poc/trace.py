"""A record of what actually produced each question.

`C360_LLM_MODE` tells you which client is configured. It does not tell you what
happened, and the gap between those two is the whole reason this module exists:

* `OpenAILLM.probe` and `.followup` catch every exception and return the bank
  template. A rate limit, a bad key, a dropped connection — all of them produce
  output identical to stub mode, with nothing logged and nothing raised.
* `OpenAILLM.judge_specificity` deliberately answers short replies from the
  deterministic filter without calling the model at all.
* `OpenAILLM.cluster_themes` always uses the fixed lexicon, by design, so that
  theme vocabulary stays comparable across rounds.

So "live mode" is not the same claim as "the model ran", and a demo that shows
only the configured mode will confidently mislead you. Every call records what it
actually did.

This is observability, not audit. It holds no answer text — only which operation
ran, which engine served it, what the outcome was and how long it took. The
buffer is in memory and bounded; nothing here is persisted, and nothing here is
on the identity side of the boundary.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("poc.llm")

MAX_CALLS = 300

# Engines
STUB = "stub"
OPENAI = "openai"

# Outcomes — what actually produced the text
MODEL = "model"            # a real API call returned it
TEMPLATE = "template"      # the bank template, unmodified
HEURISTIC = "heuristic"    # the deterministic filter answered
FALLBACK = "fallback"      # the API call raised; template/heuristic used instead
LEXICON = "lexicon"        # the fixed theme vocabulary, by design

OUTCOME_LABEL = {
    MODEL: "model call",
    TEMPLATE: "bank template",
    HEURISTIC: "deterministic filter",
    FALLBACK: "fell back after an error",
    LEXICON: "fixed lexicon",
}


@dataclass(frozen=True)
class Call:
    seq: int
    at: str
    op: str            # probe | followup | judge | themes
    engine: str        # stub | openai
    outcome: str
    ms: int
    model: str = ""
    detail: str = ""   # exception class on a fallback, or why a call was skipped
    subject: str = ""  # competency or question the call was for

    @property
    def label(self) -> str:
        return OUTCOME_LABEL.get(self.outcome, self.outcome)

    @property
    def is_live(self) -> bool:
        """True only when the model genuinely produced this."""
        return self.outcome == MODEL

    @property
    def is_problem(self) -> bool:
        return self.outcome == FALLBACK


_lock = threading.Lock()
_calls: deque[Call] = deque(maxlen=MAX_CALLS)
_seq = 0


def record(op: str, engine: str, outcome: str, ms: float, model: str = "",
           detail: str = "", subject: str = "") -> Call:
    global _seq
    with _lock:
        _seq += 1
        call = Call(
            seq=_seq,
            at=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            op=op, engine=engine, outcome=outcome, ms=int(ms),
            model=model, detail=detail, subject=subject,
        )
        _calls.append(call)

    level = logging.WARNING if call.is_problem else logging.INFO
    logger.log(
        level,
        "llm %-8s %-6s %-9s %5dms%s%s",
        call.op, call.engine, call.outcome, call.ms,
        f" [{call.subject}]" if call.subject else "",
        f" ({call.detail})" if call.detail else "",
    )
    return call


def latest_seq() -> int:
    with _lock:
        return _seq


def since(seq: int) -> tuple[Call, ...]:
    """Calls recorded after `seq` — one request's worth, taken as a diff.

    A diff rather than a context variable: the graph invokes nodes on the calling
    thread, so everything a turn does lands between two reads of the counter, and
    there is no state to install or forget to clear.
    """
    with _lock:
        return tuple(c for c in _calls if c.seq > seq)


def snapshot(limit: int = 50) -> tuple[Call, ...]:
    """Most recent calls, newest first."""
    with _lock:
        return tuple(reversed(list(_calls)))[:limit]


def clear() -> None:
    global _seq
    with _lock:
        _calls.clear()
        _seq = 0


def configure_logging(level: int = logging.INFO) -> None:
    """Make the records visible on a console.

    Library code does not own the root logger, so this is opt-in and attaches a
    handler only to this module's logger, only once.
    """
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
