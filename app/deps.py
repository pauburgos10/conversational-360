"""Cross-cutting request concerns: who is asking, and whether the model is available.

The role comes from the signed session `app.auth` issues at sign-in. It used to
come from a cookie the visitor set with a dropdown, which is why `require_hr` is
written as a dependency: the routes never cared where the role came from, so
replacing that source moved no route code.

There is no longer a model-mode switch. The demo's value is the conversation the
model conducts, so it runs live whenever a key is configured; without one it says
so in a banner instead of offering an "offline" mode as if it were a feature.
`C360_LLM_MODE=stub` still forces the deterministic client — the test suite runs
on it — but nothing in the interface sets it.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from poc import config

from . import auth
from .auth import ROLE_HR, ROLE_RATER, ROLES  # noqa: F401  (re-exported for callers)


def current_session(request: Request) -> dict | None:
    return auth.decode_session(request.cookies.get(auth.SESSION_COOKIE))


def current_role(request: Request) -> str | None:
    """The signed-in role, or None when signed out."""
    session = current_session(request)
    return session["role"] if session else None


def require_hr(request: Request) -> str:
    """Gate the reporting and administration side.

    Reports are the one surface where the audience matters: a rater seeing a
    department report learns things about colleagues that no one consented to
    share. Signed-out requests never get here — the sign-in middleware redirects
    them first — so this only has to tell a rater no.
    """
    role = current_role(request)
    if role != ROLE_HR:
        raise HTTPException(status_code=403, detail="This page is for HR admins only.")
    return role


def llm_status() -> dict:
    """Whether the model is actually in use, and why not when it is not.

    `reason` separates the two ways to be without it: no key configured (the case
    the banner is for), and the deterministic client forced on purpose.
    """
    settings = config.llm_settings()
    live = settings.mode == "openai"
    if live:
        reason = None
    elif not settings.api_key:
        reason = "no_key"
    else:
        reason = "forced_offline"
    return {"live": live, "model": settings.model, "has_key": bool(settings.api_key),
            "reason": reason}
