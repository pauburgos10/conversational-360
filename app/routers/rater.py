"""The rater journey: GET the link, POST one answer at a time.

This router deliberately knows nothing about aggregation, reporting, or the
identity side. It drives `poc.session.Interview` and renders whatever `pending`
dict comes back — which is why adding a question type to the bank needs no change
here. `tests/test_web.py` asserts that ignorance against the import graph.

Every review starts from a link HR minted for one subject (`rounds.mint_invites`)
and sent to one reviewer. There is no route that starts a review on its own: an
id nobody minted is refused rather than quietly opening a review of whoever is
first on the roster.

The URL is the resume handle. `GET /b/{ballot_id}` on an interview already in
progress re-presents the checkpointed question rather than advancing, so a
refresh, a back button, or reopening the tab tomorrow all do the same safe thing.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from poc import trace
from poc.graph import nodes
from poc.session import (
    DEFAULT_ROUND, EditRefused, Interview, subject_display_name,
)

from .. import deps, rounds

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


def _interview(ballot_id: str, round_id: str) -> Interview | None:
    """The conversation behind a ballot link, about the right person — or None.

    Who a ballot is about is settled once, when it starts, and kept in the
    checkpoint, so for a ballot already underway that decides and the link needs
    nothing but its id. A ballot with no checkpoint yet must be a minted invite,
    which knows its subject.

    Neither means the id was never minted. Earlier this fell back to S1, so a
    mistyped or invented link started a review "of" the first subject on the
    roster. Ballots started from the old "Start a review" button are not in the
    invite file but have a checkpoint, so they still resume.
    """
    interview = Interview(ballot_id, "S1", round_id)
    subject_id = (interview.saved_subject_id
                  or rounds.subject_for_ballot(ballot_id, round_id))
    if subject_id is None:
        return None
    interview.subject_id = subject_id
    return interview


_UNKNOWN_LINK = ("This link isn't valid. Check you have the whole link HR sent you, "
                 "or ask them to send it again.")


@router.get("/b/{ballot_id}", response_class=HTMLResponse)
def chat(request: Request, ballot_id: str, round_id: str = DEFAULT_ROUND,
         notice: str | None = None):
    interview = _interview(ballot_id, round_id)
    if interview is None:
        return _templates(request).TemplateResponse(
            request, "error.html", {"message": _UNKNOWN_LINK}, status_code=404
        )

    # Everything the graph does for this turn lands between these two reads.
    before = trace.latest_seq()
    try:
        turn = interview.open()
    except LookupError as exc:
        return _templates(request).TemplateResponse(
            request, "error.html", {"message": str(exc)}, status_code=404
        )

    return _templates(request).TemplateResponse(
        request,
        "chat.html",
        {
            "ballot_id": ballot_id,
            "subject_id": interview.subject_id,
            "subject_display": subject_display_name(interview.subject_id),
            # HR can open any minted link — useful for trying the experience —
            # but what they submit is a real review, and they are told so.
            "answering_as_hr": deps.current_role(request) == deps.ROLE_HR,
            "round_id": round_id,
            "turn": turn,
            "calls": trace.since(before),
            "review": _review_for(interview, turn),
            "edit_notice": notice,
        },
    )


def _review_for(interview: Interview, turn) -> dict | None:
    """The editable summary, built only when the review question is the one on screen."""
    if turn.pending and turn.pending.get("kind") == "review":
        return interview.summary_for_review()
    return None


@router.post("/b/{ballot_id}/reply", response_class=HTMLResponse)
def reply(request: Request, ballot_id: str, reply: str = Form(...),
          round_id: str = DEFAULT_ROUND):
    interview = _interview(ballot_id, round_id)

    # Guard the case where a fragment POST arrives for a thread that has no
    # checkpoint — a restarted server with a stale tab open, most likely.
    if interview is None or not interview.exists:
        return _templates(request).TemplateResponse(
            request, "error.html",
            {"message": "This conversation is no longer open. Start a new one from the round page."},
            status_code=409,
        )

    before = trace.latest_seq()
    turn = interview.send(reply)

    # Echo what they typed, because that is now what gets stored and what HR will
    # read. The redactor still runs here, but only to decide whether to warn: if
    # any rule fired, the note tells them which details will be stripped from the
    # subject's copy.
    #
    # The previous version echoed the *masked* text and called it "what will be
    # stored", which stopped being true when masking moved to render time. A
    # rater who is told their email was removed, and whose email is in fact kept
    # and shown to HR, has been actively misled — the one outcome this bubble
    # exists to prevent.
    shown = nodes.redactor().apply(reply)

    return _templates(request).TemplateResponse(
        request,
        "_turn.html",
        {
            "ballot_id": ballot_id,
            "said": reply,
            "said_redacted": bool(shown.codes),
            "turn": turn,
            "calls": trace.since(before),
            "review": _review_for(interview, turn),
        },
    )


# ---------------------------------------------------------------------------
# editing an earlier answer
# ---------------------------------------------------------------------------
#
# Plain form posts and a redirect, not HTMX fragments. An edit can re-plan the
# probes and drop the rater back into the interview with a question that did not
# exist a moment ago, so the response is a different screen rather than a patch
# to this one. Re-rendering from the checkpoint is the only way that is reliably
# right in both outcomes.


def _after_edit(ballot_id: str, notice: str | None = None) -> RedirectResponse:
    suffix = f"?notice={quote(notice)}" if notice else ""
    return RedirectResponse(f"/b/{ballot_id}{suffix}", status_code=303)


@router.post("/b/{ballot_id}/edit/rating")
def edit_rating(ballot_id: str, item_id: str = Form(...), value: int = Form(...),
                round_id: str = DEFAULT_ROUND):
    interview = _interview(ballot_id, round_id)
    if interview is None or not interview.exists:
        return _after_edit(ballot_id, "That conversation is no longer open.")
    try:
        interview.edit_rating(item_id, value)
    except EditRefused as exc:
        return _after_edit(ballot_id, str(exc))
    return _after_edit(ballot_id)


@router.post("/b/{ballot_id}/edit/answer")
def edit_answer(ballot_id: str, ref: str = Form(...), text: str = Form(...),
                round_id: str = DEFAULT_ROUND):
    interview = _interview(ballot_id, round_id)
    if interview is None or not interview.exists:
        return _after_edit(ballot_id, "That conversation is no longer open.")
    try:
        interview.edit_answer(ref, text)
    except EditRefused as exc:
        return _after_edit(ballot_id, str(exc))
    return _after_edit(ballot_id)
