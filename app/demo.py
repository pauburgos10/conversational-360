"""Demo-deployment concerns: saying on the page that the data is invented.

`is_demo` drives a banner saying every person here is synthetic, because the
subjects have plausible human names and a reader who assumes otherwise would draw
conclusions about people who do not exist.

The shared HTTP Basic password that used to live here is gone: `app.auth` signs
people in to a rater or an HR admin account instead, which gates the site and
decides the role in one step.

Off unless its environment variable is set, so local development and the test
suite are untouched.
"""

from __future__ import annotations

import os

BANNER_ENV = "C360_DEMO_BANNER"


def is_demo() -> bool:
    return os.getenv(BANNER_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def is_persistent() -> bool:
    """Whether what visitors enter survives a restart.

    Keyed on `C360_ROSTER_IN_DATA` because that is set exactly when `data/` is on
    a persistent disk — the roster only needs moving there when something is
    being persisted. The banner uses it so it never tells a visitor their work
    will be wiped when it will not, or kept when it will not.
    """
    return os.getenv("C360_ROSTER_IN_DATA", "").strip().lower() in {"1", "true", "yes", "on"}
