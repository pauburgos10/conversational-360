"""Paths and settings. Everything configurable lives in inputs/, not in code."""

from __future__ import annotations

import json
import os
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
SUBJECTS_PATH = INPUTS / "subjects.csv"

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
