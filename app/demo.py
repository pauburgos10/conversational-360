"""Demo-deployment concerns: a shared password, and saying so on the page.

None of this is the RBAC PLAN.md §3 describes. The role in the header is still a
cookie the visitor sets themselves, which means anyone who gets past the password
can read every report. That is acceptable for a synthetic round and unacceptable
for a real one, so the two controls here exist to keep the deployment honest
about which it is:

* `require_password` puts one shared credential in front of the whole site, so a
  demo URL is not publicly crawlable while it is being passed around.
* `is_demo` drives a banner saying the data is invented, because the subjects
  have plausible human names and a reader who assumes otherwise would draw
  conclusions about people who do not exist.

Both are off unless their environment variables are set, so local development and
the test suite are untouched.
"""

from __future__ import annotations

import os
import secrets

from starlette.responses import PlainTextResponse

USER_ENV = "C360_DEMO_USER"
PASSWORD_ENV = "C360_DEMO_PASSWORD"
BANNER_ENV = "C360_DEMO_BANNER"

# Unauthenticated paths. The health check has to answer before the platform will
# route traffic at all, and a 401 there reads as a failed deploy.
OPEN_PATHS = ("/healthz",)


def credentials() -> tuple[str, str] | None:
    user = os.getenv(USER_ENV, "").strip()
    password = os.getenv(PASSWORD_ENV, "").strip()
    return (user, password) if user and password else None


def is_demo() -> bool:
    return os.getenv(BANNER_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _authorised(header: str, expected: tuple[str, str]) -> bool:
    import base64
    import binascii

    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False

    user, _, password = decoded.partition(":")
    # Both halves compared, and both with compare_digest: a plain `==` on the
    # username leaks its length through timing, and checking only the password
    # would accept any username at all.
    return (secrets.compare_digest(user, expected[0])
            and secrets.compare_digest(password, expected[1]))


async def require_password(request, call_next):
    """Shared-secret gate over the whole site, or a no-op when unconfigured."""
    expected = credentials()
    if expected is None or request.url.path in OPEN_PATHS:
        return await call_next(request)

    header = request.headers.get("authorization", "")
    if _authorised(header, expected):
        return await call_next(request)

    return PlainTextResponse(
        "This demo is password-protected.",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Conversational 360 demo"'},
    )
