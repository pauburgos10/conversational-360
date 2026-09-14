"""Paths and settings. Everything configurable lives in inputs/, not in code."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# .env.example ships with this POC, so something has to read .env or that file
# is a lie. `override=False` means a real environment variable still wins, which
# keeps CI and the test suite in control of their own settings.
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:      # python-dotenv is optional; stub mode needs no config
    pass
INPUTS = ROOT / "inputs"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

COMPETENCIES_PATH = INPUTS / "competencies.json"
QUESTIONS_PATH = INPUTS / "questions.json"
RULES_PATH = INPUTS / "reporting-rules.yaml"
CONSENT_PATH = INPUTS / "consent.md"
GAZETTEER_PATH = INPUTS / "name_gazetteer.txt"

# The roster ships in inputs/, but it is the one file there the app WRITES —
# `roster.add_subject` appends to it. That makes its location a correctness
# question, not a tidiness one, the moment `data/` is on persistent storage and
# `inputs/` is not (a Render disk mounted at /app/data, say):
#
#   a subject added through /admin survives in `responses.db`, their ballots
#   survive, but the roster reverts to the bundled copy on restart — and
#   `roster.redaction_names()` builds the masking list from the roster. The
#   person's name silently drops out of it, and appears unmasked in the
#   subject's copy of every report written about them from then on.
#
# `C360_ROSTER_IN_DATA` moves the live roster next to the database, so the two
# persist or vanish together. It is a switch rather than a free-form path on
# purpose: a path could be pointed somewhere off the disk and recreate the split
# it exists to prevent.
BUNDLED_SUBJECTS_PATH = INPUTS / "subjects.csv"


def roster_path(data: Path | None = None, bundled: Path | None = None) -> Path:
    """Where the live roster lives.

    Off (the default): the bundled `inputs/subjects.csv`, unchanged — local work
    and the test suite are unaffected.

    On: `data/subjects.csv`, created from the bundled copy the first time and
    **never overwritten after that**. A restart must not revert subjects added at
    runtime; that reversion is the leak. The consequence to know about: once the
    live copy exists, edits to `inputs/subjects.csv` in a later deploy are not
    picked up — add the subject through /admin, or delete the live copy.
    """
    data = data or DATA
    bundled = bundled or BUNDLED_SUBJECTS_PATH
    if os.getenv("C360_ROSTER_IN_DATA", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return bundled
    live = data / "subjects.csv"
    if not live.exists() and bundled.exists():
        shutil.copyfile(bundled, live)
    return live


SUBJECTS_PATH = roster_path()

RESPONSES_DB = DATA / "responses.db"
CHECKPOINTS_DB = DATA / "checkpoints.db"


@lru_cache(maxsize=1)
def rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def consent_text() -> str:
    return CONSENT_PATH.read_text(encoding="utf-8")


@dataclass(frozen=True)
class LLMSettings:
    mode: str          # "stub" | "openai"
    model: str
    api_key: str | None

    @property
    def is_stub(self) -> bool:
        return self.mode == "stub"


def llm_settings() -> LLMSettings:
    """`auto` resolves to openai when a key is present, otherwise stub.

    The POC is designed to run and be tested with no key and no network. That is
    not only about cost: it means the tests below are hermetic, and the guardrail
    and suppression behaviour is identical in CI and in a demo.
    """
    requested = os.getenv("C360_LLM_MODE", "auto").strip().lower()
    key = os.getenv("OPENAI_API_KEY") or None
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if requested == "auto":
        mode = "openai" if key else "stub"
    else:
        mode = requested
    if mode == "openai" and not key:
        mode = "stub"
    return LLMSettings(mode=mode, model=model, api_key=key)
