"""The demo deployment says, on every page, that its data is invented.

The shared HTTP Basic password these tests used to cover is gone; signing in to a
rater or HR admin account replaced it, and `test_sign_in.py` covers that. What is
left is the banner — including on the sign-in page, which is the first thing a
visitor sees.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.main import app


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv(demo.BANNER_ENV, raising=False)
    return TestClient(app)


def test_the_banner_declares_the_data_synthetic(monkeypatch):
    monkeypatch.setenv(demo.BANNER_ENV, "1")
    page = TestClient(app).get("/").text
    assert "synthetic" in page
    assert "no real employee is" in page


def test_the_banner_is_absent_when_not_a_demo(unconfigured):
    assert "no real employee is" not in unconfigured.get("/").text


def test_the_banner_warns_of_wiping_only_when_nothing_persists(monkeypatch):
    """A banner promising a wipe on a persistent disk would be false on every page."""
    monkeypatch.setenv(demo.BANNER_ENV, "1")

    monkeypatch.delenv("C360_ROSTER_IN_DATA", raising=False)
    ephemeral = TestClient(app).get("/").text
    assert "wiped when the demo restarts" in ephemeral

    monkeypatch.setenv("C360_ROSTER_IN_DATA", "1")
    persistent = TestClient(app).get("/").text
    assert "wiped when the demo restarts" not in persistent
    assert "kept between visits" in persistent
    assert "test names and" in persistent, "persistence makes real names a real risk"
