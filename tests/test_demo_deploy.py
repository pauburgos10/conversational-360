"""The demo deployment's two controls: a shared password, and saying it is fake.

Neither is real security — the role in the header is still a cookie the visitor
sets, so anyone past the password can read every report. That is why these tests
also pin the things that keep the deployment honest about what it is: the gate
must be OFF unless configured (so local work and this suite are untouched), and
ON for every path but the health check once it is.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.main import app


@pytest.fixture
def unconfigured(monkeypatch):
    for var in (demo.USER_ENV, demo.PASSWORD_ENV, demo.BANNER_ENV):
        monkeypatch.delenv(var, raising=False)
    return TestClient(app)


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv(demo.USER_ENV, "hr")
    monkeypatch.setenv(demo.PASSWORD_ENV, "correct horse")
    return TestClient(app)


def test_the_gate_is_off_unless_both_variables_are_set(unconfigured):
    """A half-configured gate must fail open, not lock the owner out of their own app."""
    assert unconfigured.get("/healthz").status_code == 200
    assert unconfigured.get("/").status_code == 200


def test_a_password_only_deployment_stays_open(monkeypatch):
    monkeypatch.setenv(demo.PASSWORD_ENV, "secret")
    monkeypatch.delenv(demo.USER_ENV, raising=False)
    assert demo.credentials() is None


def test_the_gate_challenges_the_browser(gated):
    r = gated.get("/")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Basic")


def test_correct_credentials_pass(gated):
    assert gated.get("/", auth=("hr", "correct horse")).status_code == 200


@pytest.mark.parametrize("creds", [
    ("hr", "wrong"), ("nobody", "correct horse"), ("", ""), ("hr", ""),
])
def test_wrong_credentials_are_refused(gated, creds):
    assert gated.get("/", auth=creds).status_code == 401


def test_the_username_is_checked_too(gated):
    """Checking only the password would accept any username at all."""
    assert gated.get("/", auth=("anyone", "correct horse")).status_code == 401


def test_the_health_check_stays_open(gated):
    """A platform probe that gets a 401 reads as a failed deploy."""
    assert gated.get("/healthz").status_code == 200


def test_report_routes_are_behind_the_gate_too(gated):
    """The reports are the sensitive surface; a gate that missed them is no gate."""
    gated.cookies.set("c360_role", "hr_admin")
    assert gated.get("/admin").status_code == 401
    assert gated.get("/admin/subject/S1").status_code == 401


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
