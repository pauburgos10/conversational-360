"""Cross-cutting request concerns: who is asking, and which LLM mode is live.

Both are deliberately mock. PLAN.md §3 puts real RBAC (OIDC, session cookies,
audit log) in Week 3; what this module provides is the *shape* of that control
so the routes are written against a dependency from the start, and swapping the
implementation later does not move any route code.

The role lives in a plain cookie a user can edit. That is fine and it is stated
on the page: this gates a demo, not a secret.

The LLM mode lives in a file rather than in the process. An earlier version set
`os.environ` once at import, which meant the toggle silently reverted to stub on
every `--reload` and every restart — the setting existed only in the memory of a
process that uvicorn replaces whenever a file is saved.
"""

from __future__ import annotations

import json
import os

from fastapi import HTTPException, Request

from poc import config

ROLE_COOKIE = "c360_role"
ROLE_RATER = "rater"
ROLE_HR = "hr_admin"
ROLES = {
    ROLE_RATER: "Rater",
    ROLE_HR: "HR / Admin",
}

# The web demo starts offline on purpose: clicking through the whole Likert battery should
# not cost anything, and stub mode is what the test suite exercises.
DEFAULT_LLM_MODE = "stub"
VALID_LLM_MODES = ("stub", "openai", "auto")
SETTINGS_PATH = config.DATA / "web-settings.json"


def current_role(request: Request) -> str:
    role = request.cookies.get(ROLE_COOKIE, ROLE_RATER)
    return role if role in ROLES else ROLE_RATER


def require_hr(request: Request) -> str:
    """Gate the reporting side.

    Reports are the one surface where the audience matters: a rater seeing a
    cohort report learns things about colleagues that no one consented to share.
    """
    role = current_role(request)
    if role != ROLE_HR:
        raise HTTPException(
            status_code=403,
            detail="Reports are visible to HR / Admin only. Switch role in the header.",
        )
    return role


# ---------------------------------------------------------------------------
# LLM mode
# ---------------------------------------------------------------------------

def _read_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def stored_llm_mode() -> str:
    """The operator's last choice, or the offline default."""
    mode = _read_settings().get("llm_mode", DEFAULT_LLM_MODE)
    return mode if mode in VALID_LLM_MODES else DEFAULT_LLM_MODE


def set_llm_mode(mode: str) -> None:
    """Record the choice so it survives a reload, and apply it to this process."""
    if mode not in VALID_LLM_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown LLM mode {mode!r}")
    _write_settings({**_read_settings(), "llm_mode": mode})
    os.environ["C360_LLM_MODE"] = mode


def apply_llm_mode() -> str:
    """Push the stored choice into the environment `poc.llm` reads.

    Called per request rather than once at import, for two reasons. Ordering: a
    route calls the model inside its handler, which runs before any template is
    rendered, so a context processor would apply the mode a beat too late.
    Durability: uvicorn's reloader replaces the worker process on every file
    save, and anything held only in module state goes with it.
    """
    mode = stored_llm_mode()
    os.environ["C360_LLM_MODE"] = mode
    return mode


def llm_status() -> dict:
    """What mode is actually in force, and whether the request was honoured.

    `requested` and `effective` differ when someone asks for openai with no key —
    `config.llm_settings()` silently falls back, and a demo that does not say so
    looks like the model is being called when it is not.
    """
    requested = stored_llm_mode()
    settings = config.llm_settings()
    return {
        "requested": requested,
        "effective": settings.mode,
        "model": settings.model,
        "has_key": bool(settings.api_key),
        "degraded": requested == "openai" and settings.mode == "stub",
    }
