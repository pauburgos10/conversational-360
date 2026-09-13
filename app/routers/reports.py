"""The results side: aggregates, reports, exports, and round controls.

Every route here depends on `require_hr`. That dependency is mock (PLAN.md puts
real RBAC in Week 3) but it is a real seam: no route reads a role from a request
itself, so replacing the implementation touches one file.

Reports are rendered from `poc.reporting`, not re-derived. The suppression
summary is shown next to them on purpose — a report full of em-dashes is only
legible once you can see how many cells were suppressed and why.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from poc import reporting, store, trace
from poc.session import DEFAULT_ROUND

from .. import deps, rounds

router = APIRouter(prefix="/admin", dependencies=[Depends(deps.require_hr)])


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, round_id: str = DEFAULT_ROUND,
              added: str | None = None, error: str | None = None):
    pipe = rounds.pipeline(round_id)
    subject_rows = rounds.subject_state(round_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "admin.html",
        {
            "round_id": round_id,
            "subjects": subject_rows,
            "summary": pipe.summary,
            "cohorts": pipe.cohorts,
            "freeze": rounds.round_freeze_info(round_id),
            "n_cells": len(pipe.cells),
            # Cohorts offered in the add form come from the roster, not from the
            # pipeline: a cohort whose subjects have no ballots yet is absent
            # from `pipe.cohorts`, and it should still be offered.
            "known_cohorts": sorted({s["cohort"] for s in subject_rows if s["cohort"]}),
            "added": next((s for s in subject_rows if s["subject_id"] == added), None),
            "error": error,
            "calls": trace.snapshot(40),
        },
    )


@router.get("/subject/{subject_id}", response_class=HTMLResponse)
def individual(request: Request, subject_id: str, round_id: str = DEFAULT_ROUND,
               audience: str = reporting.AUDIENCE_HR):
    """The individual report, in either of its two renderings.

    HR is the default here because this route sits behind `require_hr` and exists
    for the people running the round. `?audience=subject` renders the copy the
    subject receives — masked and unattributed — so HR can see exactly what they
    are about to hand over before they hand it over.
    """
    pipe = rounds.pipeline(round_id)
    if subject_id not in pipe.data["subjects"]:
        return request.app.state.templates.TemplateResponse(
            request, "error.html", {"message": f"Unknown subject {subject_id!r}."}, status_code=404
        )
    audience = (reporting.AUDIENCE_HR if audience == reporting.AUDIENCE_HR
                else reporting.AUDIENCE_SUBJECT)
    text = reporting.individual_report(
        subject_id, pipe.data, pipe.cells, pipe.marks, pipe.rules, pipe.bank,
        audience=audience,
        raters=rounds.rater_labels(round_id) if audience == reporting.AUDIENCE_HR else None,
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "report.html",
        {
            "title": pipe.data["subjects"][subject_id]["display_name"],
            "kind": ("Individual report — HR copy" if audience == reporting.AUDIENCE_HR
                     else "Individual report — subject copy"),
            "body": text,
            "round_id": round_id,
            "subject_id": subject_id,
            "audience": audience,
        },
    )


@router.get("/cohort/{cohort}", response_class=HTMLResponse)
def cohort(request: Request, cohort: str, round_id: str = DEFAULT_ROUND):
    pipe = rounds.pipeline(round_id)
    text = reporting.cohort_report(
        cohort, pipe.data, pipe.cells, pipe.marks, pipe.rules, pipe.bank,
        audience=reporting.AUDIENCE_HR,
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "report.html",
        {"title": cohort, "kind": "Cohort report", "body": text, "round_id": round_id},
    )


@router.get("/aggregates.csv", response_class=PlainTextResponse)
def aggregates(round_id: str = DEFAULT_ROUND):
    pipe = rounds.pipeline(round_id)
    csv_text = reporting.aggregates_csv(pipe.cells, pipe.marks, pipe.rules)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="aggregates-{round_id}.csv"'},
    )


@router.post("/freeze")
def freeze(round_id: str = Form(DEFAULT_ROUND)):
    """Close the window and record a digest.

    Re-freezing is allowed and recomputes: a ballot submitted after a freeze is
    still counted by `fetch_round`, so a stale digest would be the misleading
    outcome, not a refreshed one.
    """
    store.freeze_round(round_id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/llm-mode")
def llm_mode(mode: str = Form(...)):
    deps.set_llm_mode(mode)
    return RedirectResponse("/admin", status_code=303)


@router.get("/invites", response_class=HTMLResponse)
def invite_list(request: Request, round_id: str = DEFAULT_ROUND,
                error: str | None = None, assigned: str | None = None):
    """Pre-minted ballot links: who each one went to, and how far it has got.

    Each row shows whether that link has been opened and whether it was
    submitted, which is what makes the min_n panel on the dashboard actionable:
    you can see there are three links out and only one came back. The assigned
    address turns that from a count into a next action — it names who is still
    owed a reminder.
    """
    rows = rounds.invite_rows(round_id)
    return request.app.state.templates.TemplateResponse(
        request,
        "invites.html",
        {
            "round_id": round_id,
            "rows": rows,
            "summary": rounds.invite_summary(rows),
            "subjects": rounds.subject_state(round_id),
            "error": error,
            "assigned": assigned,
        },
    )


@router.post("/invites")
def mint(subject_id: str = Form(...), n: int = Form(2), round_id: str = Form(DEFAULT_ROUND),
         email: str = Form("")):
    try:
        rounds.mint_invites(subject_id, n, round_id, email=email)
    except ValueError as exc:
        return RedirectResponse(f"/admin/invites?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/admin/invites", status_code=303)


@router.post("/invites/assign")
def assign(ballot_id: str = Form(...), email: str = Form(""),
           round_id: str = Form(DEFAULT_ROUND)):
    """Attach or change the address a link was handed to.

    Separate from minting because the two happen at different times: links get
    minted when the round opens and handed out as HR works out who is reviewing
    whom, which is often days later and rarely all at once.
    """
    try:
        rounds.assign_email(ballot_id, email, round_id)
    except (ValueError, LookupError) as exc:
        return RedirectResponse(f"/admin/invites?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(f"/admin/invites?assigned={quote(ballot_id)}", status_code=303)
