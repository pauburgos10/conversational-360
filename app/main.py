"""FastAPI app — the manual-test surface for the POC.

    ./.venv360/bin/python -m uvicorn app.main:app --reload

This is PLAN.md Week 1–2 in its thinnest honest form: the conversation engine and
the aggregation pipeline, wrapped in HTTP and given a UI, with no new logic of
its own. Every number on every page comes from `poc/`.

Two things it deliberately does NOT do, both from PLAN.md §3 and both Week 1+
work: there is no `identity.db` and no magic-link token exchange, and the role in
the header is a cookie rather than a session. Both are labelled as mocks in the
UI rather than left to be discovered.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup, escape
from starlette.exceptions import HTTPException

from poc import store, trace
from poc.session import DEFAULT_ROUND

from . import demo, deps, rounds
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
    deps.apply_llm_mode()
    # Every model call also goes to the console, so a fallback is visible in the
    # terminal even when nobody is looking at a page.
    trace.configure_logging()
    yield


app = FastAPI(title="Conversational 360°", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.state.templates = templates


app.middleware("http")(demo.require_password)


@app.get("/healthz")
def healthz():
    """Liveness probe. Deliberately outside the password gate."""
    return {"ok": True}


@app.middleware("http")
async def apply_stored_settings(request: Request, call_next):
    """Restore the operator's LLM choice before the handler can call a model.

    Per request, because the worker process is not durable: `--reload` replaces
    it on every file save, and a mode held in module state would revert to stub
    without saying so.
    """
    deps.apply_llm_mode()
    response = await call_next(request)

    # Every page here reflects mutable round state — the active LLM mode, ballot
    # counts, freeze status. A browser serving one of these from cache after a
    # redirect shows a stale setting and reads as a control that did nothing.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        request, "error.html", {"message": exc.detail}, status_code=exc.status_code
    )


@app.get("/", response_class=HTMLResponse)
def landing(request: Request, round_id: str = DEFAULT_ROUND):
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "round_id": round_id,
            "subjects": rounds.subject_state(round_id),
            "freeze": rounds.round_freeze_info(round_id),
        },
    )


@app.post("/role")
def set_role(request: Request, role: str = Form(...), next: str = Form("/")):
    # Only same-site paths, so a crafted `next` cannot bounce someone off-site.
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(deps.ROLE_COOKIE, role if role in deps.ROLES else deps.ROLE_RATER)
    return response


app.include_router(rater.router)
app.include_router(selfassess.router)
app.include_router(reports.router)
app.include_router(subjects.router)
