"""FastAPI app — the manual-test surface for the POC.

    ./.venv360/bin/python -m uvicorn app.main:app --reload

This is PLAN.md Week 1–2 in its thinnest honest form: the conversation engine and
the aggregation pipeline, wrapped in HTTP and given a UI, with no new logic of
its own. Every number on every page comes from `poc/`.

Everyone signs in to one of two accounts, a rater or an HR admin (`app.auth`).
A rater reaches a review only through a ballot link HR minted and sent them;
everything else — subjects, links, reports, settings — is HR's side.

Still not production identity: one shared account per role, no `identity.db`,
no per-person magic links. PLAN.md §3 describes the real version.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup, escape
from starlette.exceptions import HTTPException

from poc import store, trace

from . import auth, demo, deps, rounds
from .routers import rater, reports, selfassess, subjects

HERE = Path(__file__).resolve().parent

_md = MarkdownIt("commonmark").enable("table")


def _paras(text: str) -> Markup:
    """Blank-line-separated plain text to <p> blocks, escaped first.

    Every string this runs on is either bank copy or a rater's own words. The
    rater's words reach here already redacted, but they are still user input, so
    they are escaped before anything else happens to them.
    """
    blocks = [b.strip() for b in str(text or "").split("\n\n") if b.strip()]
    return Markup("".join(f"<p>{escape(b)}</p>".replace("\n", "<br>") for b in blocks))


def _chrome(request: Request) -> dict:
    """Header context for every template.

    A context processor rather than a middleware writing `env.globals`: globals
    are shared across requests, so two people with different roles would race
    each other into the wrong header.
    """
    return {
        "role": deps.current_role(request),
        "roles": deps.ROLES,
        "llm": deps.llm_status(),
        "is_demo": demo.is_demo(),
        "demo_persistent": demo.is_persistent(),
    }


templates = Jinja2Templates(directory=HERE / "templates", context_processors=[_chrome])
templates.env.filters["paras"] = _paras
templates.env.filters["md"] = lambda text: Markup(_md.render(str(text or "")))


def _seed_if_empty() -> None:
    """Populate a fresh deployment with the synthetic round.

    Free hosting tiers give a container ephemeral storage, so a restart comes
    back with no database and every report reading "not enough responses" —
    which looks like a broken deploy rather than the min_n rule. Seeding on an
    empty store makes a cold start self-healing.

    Guarded on *empty*, never on missing-file alone: re-seeding wipes
    `responses.db`, and doing that to a running demo would delete whatever the
    person clicking around had just entered.
    """
    with store.connect() as conn:
        if conn.execute("SELECT COUNT(*) c FROM ballot").fetchone()["c"]:
            return
    from poc import seed_ballots
    seed_ballots.seed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    if os.getenv("C360_SEED_ON_BOOT", "").strip().lower() in {"1", "true", "yes", "on"}:
        _seed_if_empty()
    # After seeding, so the synthetic round's completed self-assessments have
    # links HR can open from the first page load.
    rounds.backfill_self_links()
    # Every model call also goes to the console, so a fallback is visible in the
    # terminal even when nobody is looking at a page.
    trace.configure_logging()
    yield


app = FastAPI(title="Conversational 360°", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.state.templates = templates


@app.get("/healthz")
def healthz():
    """Liveness probe. Deliberately outside the password gate."""
    return {"ok": True}


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)

    # Every page here reflects mutable round state — ballot counts, link status,
    # freeze status. A browser serving one of these from cache after a redirect
    # shows a stale page and reads as a control that did nothing.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        request, "error.html", {"message": exc.detail}, status_code=exc.status_code
    )


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    """Every page but the sign-in form needs a session.

    Registered after `no_store`, which makes it the outer middleware: a signed-out
    request is turned away before any handler — or any model call — runs. The
    page asked for rides along as `next`, so a rater opening their ballot link
    signs in and lands on that review rather than on a home page.

    An HTMX request (a chat reply posted after the session lapsed) gets a header
    telling the browser to navigate, since swapping a sign-in page into the
    middle of the conversation would read as a broken page.
    """
    if auth.is_public(request.url.path) or deps.current_session(request):
        return await call_next(request)

    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    login = f"/login?next={quote(target)}"
    if request.headers.get("hx-request"):
        return Response(status_code=401, headers={"HX-Redirect": login})
    return RedirectResponse(login, status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str | None = None):
    if deps.current_session(request):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request, "login.html",
        {"next": auth.safe_next(next), "error": error, "configured": bool(auth.accounts())},
    )


@app.post("/login")
def login(username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    role = auth.authenticate(username.strip(), password)
    if role is None:
        # One message for every failure: naming which half was wrong tells a
        # guesser that the other half was right.
        return RedirectResponse(
            f"/login?next={quote(auth.safe_next(next))}&error=1", status_code=303
        )
    response = RedirectResponse(auth.safe_next(next), status_code=303)
    response.set_cookie(auth.SESSION_COOKIE, auth.encode_session(role, username.strip()),
                        httponly=True, samesite="lax")
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """HR lands on the round dashboard; a rater on how to reach their reviews.

    A rater is deliberately not shown a list of subjects. Who reviews whom is
    HR's decision, made by minting a link for a particular person and sending it
    to the reviewer — a list here would let anyone signed in as the rater account
    start a review of anyone.
    """
    if deps.current_role(request) == deps.ROLE_HR:
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "landing.html", {})


app.include_router(rater.router)
app.include_router(selfassess.router)
app.include_router(reports.router)
app.include_router(subjects.router)
