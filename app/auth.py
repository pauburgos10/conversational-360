"""Two app accounts — a rater and an HR admin — and the session that carries them.

This replaces two earlier controls at once: a shared HTTP Basic password over the
whole site, and a role picked from a dropdown in the header and stored in a plain
cookie the visitor could edit. The second meant anyone past the password could
read every report by choosing "HR / Admin". Now the role comes from which account
signed in, and the cookie carrying it is signed, so editing it logs you out
rather than promoting you.

Still a demo control, not production auth: one shared account per role, set in
the environment. Each account is configured by a username and password pair; an
account with either half missing does not exist. With no account configured at
all, nobody can sign in — the app fails closed rather than falling back to a
default password a forgotten environment variable would expose.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

ROLE_RATER = "rater"
ROLE_HR = "hr_admin"
ROLES = {
    ROLE_RATER: "Rater",
    ROLE_HR: "HR admin",
}

SESSION_COOKIE = "c360_session"
SECRET_ENV = "C360_SESSION_SECRET"
ACCOUNT_ENV = {
    ROLE_RATER: ("C360_RATER_USER", "C360_RATER_PASSWORD"),
    ROLE_HR: ("C360_HR_USER", "C360_HR_PASSWORD"),
}

# Reachable signed out: the login form itself, the health check (a redirect there
# reads to the platform as a failed deploy), and the stylesheet the login page uses.
PUBLIC_PATHS = ("/login", "/healthz")
PUBLIC_PREFIXES = ("/static/",)


def accounts() -> dict[str, tuple[str, str]]:
    """Configured accounts by role. Read per call so a test or a restart can change them."""
    found = {}
    for role, (user_env, password_env) in ACCOUNT_ENV.items():
        user = os.getenv(user_env, "").strip()
        password = os.getenv(password_env, "").strip()
        if user and password:
            found[role] = (user, password)
    return found


def authenticate(username: str, password: str) -> str | None:
    """The role these credentials sign in as, or None.

    Every configured account is checked, and both halves with `compare_digest`:
    stopping at the first match, or comparing the username with `==`, would let
    response timing say which usernames exist.
    """
    matched = None
    for role, (user, expected) in accounts().items():
        user_ok = secrets.compare_digest(username.encode(), user.encode())
        password_ok = secrets.compare_digest(password.encode(), expected.encode())
        if user_ok and password_ok:
            matched = role
    return matched


def _secret() -> bytes:
    """The key sessions are signed with.

    `C360_SESSION_SECRET` when set. Otherwise derived from the configured
    credentials, which keeps sessions valid across a restart without one more
    variable to set — and changing a password then signs everyone out, which is
    what changing a password should do.
    """
    explicit = os.getenv(SECRET_ENV, "").strip()
    if explicit:
        return explicit.encode()
    material = json.dumps(sorted(accounts().items())).encode()
    return hashlib.sha256(b"c360-session|" + material).digest()


def _sign(payload: bytes) -> str:
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def encode_session(role: str, username: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"role": role, "user": username}).encode()
    ).decode()
    return f"{payload}.{_sign(payload.encode())}"


def decode_session(cookie: str | None) -> dict | None:
    """The session in a cookie, or None if it is missing, tampered with or stale.

    Stale means the account it names no longer exists or has been renamed — a
    session must not outlive the credentials that created it.
    """
    if not cookie or "." not in cookie:
        return None
    payload, _, signature = cookie.rpartition(".")
    if not hmac.compare_digest(signature, _sign(payload.encode())):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, json.JSONDecodeError):
        return None
    configured = accounts().get(data.get("role"))
    if not configured or configured[0] != data.get("user"):
        return None
    return data


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def safe_next(target: str | None) -> str:
    """Only same-site paths, so a crafted `next` cannot bounce someone off-site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"
