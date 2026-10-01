"""Signing in: two accounts, a signed session, and nothing reachable without one.

These replace the shared HTTP Basic password and the role dropdown in the header.
The dropdown was the weak point: it stored the role in a cookie the visitor set,
so anyone past the password could read every report by choosing "HR / Admin".

Only pages that read no round data are requested here, so no store is touched.
Permission tests that need a round live in `test_web.py`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from tests.conftest import HR_LOGIN, RATER_LOGIN, sign_in_as_hr, sign_in_as_rater


@pytest.fixture
def anon():
    return TestClient(app)


def test_a_signed_out_visitor_is_sent_to_sign_in_with_the_page_they_wanted(anon):
    r = anon.get("/b/w-some-link", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/b/w-some-link"


def test_signing_in_returns_to_the_page_that_was_asked_for(anon):
    """A rater opening their ballot link lands on that review, not a home page."""
    r = anon.post("/login", data={"username": RATER_LOGIN[0], "password": RATER_LOGIN[1],
                                  "next": "/b/w-some-link"}, follow_redirects=False)
    assert r.headers["location"] == "/b/w-some-link"


def test_next_cannot_send_anyone_off_site(anon):
    r = anon.post("/login", data={"username": RATER_LOGIN[0], "password": RATER_LOGIN[1],
                                  "next": "//evil.example/phish"}, follow_redirects=False)
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("username,password", [
    (RATER_LOGIN[0], "wrong"),
    ("nobody", RATER_LOGIN[1]),
    (RATER_LOGIN[0], HR_LOGIN[1]),     # one account's name with the other's password
    ("", ""),
])
def test_wrong_credentials_are_refused_with_one_message(anon, username, password):
    r = anon.post("/login", data={"username": username, "password": password})
    assert "did not match an account" in r.text
    assert auth.SESSION_COOKIE not in anon.cookies


def test_each_account_signs_in_as_its_own_role():
    assert auth.authenticate(*RATER_LOGIN) == auth.ROLE_RATER
    assert auth.authenticate(*HR_LOGIN) == auth.ROLE_HR


def test_with_no_accounts_configured_nobody_gets_in(anon, monkeypatch):
    """Fails closed: no default password for a forgotten variable to expose."""
    for user_env, password_env in auth.ACCOUNT_ENV.values():
        monkeypatch.delenv(user_env, raising=False)
        monkeypatch.delenv(password_env, raising=False)
    assert auth.accounts() == {}
    assert auth.authenticate("", "") is None
    assert "No accounts are configured" in anon.get("/login").text


def test_an_account_with_half_its_credentials_does_not_exist(monkeypatch):
    monkeypatch.delenv("C360_HR_PASSWORD")
    assert auth.ROLE_HR not in auth.accounts()
    assert auth.authenticate(HR_LOGIN[0], "") is None


def test_an_edited_session_cookie_signs_you_out_rather_than_promoting_you(anon):
    """The old role cookie could be edited to "hr_admin". A signed one cannot."""
    sign_in_as_rater(anon)
    cookie = anon.cookies[auth.SESSION_COOKIE]
    payload, _, signature = cookie.rpartition(".")
    forged = auth.encode_session(auth.ROLE_HR, HR_LOGIN[0]).rpartition(".")[0]
    anon.cookies.set(auth.SESSION_COOKIE, f"{forged}.{signature}")

    r = anon.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_a_session_does_not_outlive_its_account(anon, monkeypatch):
    """Renaming or removing an account must end the sessions it issued."""
    # A fixed secret, so the signature stays valid across the rename and it is the
    # account check, not a changed derived key, that ends the session.
    monkeypatch.setenv("C360_SESSION_SECRET", "fixed")
    sign_in_as_rater(anon)
    assert auth.decode_session(anon.cookies[auth.SESSION_COOKIE]) is not None
    monkeypatch.setenv("C360_RATER_USER", "someone.else")
    assert auth.decode_session(anon.cookies[auth.SESSION_COOKIE]) is None


def test_signing_out_ends_the_session(anon):
    sign_in_as_rater(anon)
    anon.post("/logout")
    assert anon.get("/", follow_redirects=False).status_code == 303


def test_a_chat_reply_after_the_session_lapsed_navigates_rather_than_swaps(anon):
    """HTMX would otherwise paste the sign-in page into the middle of the chat."""
    r = anon.post("/b/w-x/reply", data={"reply": "yes"}, headers={"HX-Request": "true"},
                  follow_redirects=False)
    assert r.status_code == 401
    assert r.headers["HX-Redirect"].startswith("/login?next=")


def test_the_health_check_needs_no_session(anon):
    """A platform probe redirected to sign-in reads as a failed deploy."""
    assert anon.get("/healthz").json() == {"ok": True}


def test_a_rater_is_welcomed_without_a_list_of_subjects(anon):
    """Who reviews whom is HR's call; the rater's home offers no one to pick."""
    page = sign_in_as_rater(anon).get("/").text
    assert "personal link" in page
    assert "/start" not in page and "Review</button>" not in page


def test_hr_lands_on_the_dashboard(anon):
    r = sign_in_as_hr(anon).get("/", follow_redirects=False)
    assert r.headers["location"] == "/admin"


def test_the_header_names_the_account_and_offers_no_role_switch(anon):
    page = sign_in_as_rater(anon).get("/").text
    assert "Signed in as <strong>Rater</strong>" in page
    assert 'action="/role"' not in page
    assert anon.post("/role", data={"role": "hr_admin"}).status_code in (404, 405)
