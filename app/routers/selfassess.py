"""The subject rating themselves.

Deliberately a form, not a conversation. `self_assessment` stores Likert values
only — there is no table that accepts self open-text, and adding one would put a
second attributable free-text store next to the anonymous pool for no reporting
benefit, since `reporting._self_section` renders numbers alone.

That matches how the client frames it: a perspective that opens the development
conversation, explicitly excluded from the average. It is not a ballot, and
making it feel like one would misrepresent what it is.

Each page saves on submit rather than accumulating in hidden fields, so an
abandoned form keeps what was answered and reopening it resumes. That is also
what makes a partly-filled form legible from the outside: `rounds.self_status`
reads the same rows and reports "in progress" rather than treating anything
short of all {n} items as nothing at all.

The form is reached only through a link HR mints for the subject
(`rounds.mint_self_link`). It used to be addressed by subject id, which with one
shared rater account let anyone signed in fill in anyone's self-assessment.

Every question here is rendered from `banks.SELF`, the second person singular
wording. The reader of this page is the person being assessed, and asking them
"does this person meet agreed deadlines?" about themselves reads as a form built
for somebody else.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from poc import banks, store
from poc.session import DEFAULT_ROUND, subject_display_name

from .. import deps, rounds

router = APIRouter(prefix="/self")


_UNKNOWN_LINK = ("This self-assessment link isn't valid. Check you have the whole link "
                 "HR sent you, or ask them to send it again.")


def _not_found(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "error.html", {"message": _UNKNOWN_LINK}, status_code=404
    )


@router.get("/{token}", response_class=HTMLResponse)
def form(request: Request, token: str, page: int = 0, round_id: str = DEFAULT_ROUND):
    subject_id = rounds.subject_for_self_token(token, round_id)
    if subject_id is None:
        return _not_found(request)
    bank = banks.load_bank()
    competencies = bank.competencies
    try:
        display = subject_display_name(subject_id)
    except LookupError as exc:
        return request.app.state.templates.TemplateResponse(
            request, "error.html", {"message": str(exc)}, status_code=404
        )

    answered = rounds.self_answers(subject_id, round_id)
    page = max(0, min(page, len(competencies)))
    done = page >= len(competencies)

    # Per-competency completion drives the jump list, so someone returning to a
    # half-finished form can see which pages still need them rather than
    # stepping through all ten to find out.
    pages = [
        {
            "index": i,
            "name": comp.name,
            "n_done": sum(1 for item in comp.items if item.id in answered),
            "n_items": len(comp.items),
            "current": i == page,
        }
        for i, comp in enumerate(competencies)
    ]

    return request.app.state.templates.TemplateResponse(
        request,
        "self.html",
        {
            "token": token,
            "subject_display": display,
            # The form has no submit step and stays editable until the round
            # closes, so HR opening it to read the answers could change them.
            "viewing_as_hr": deps.current_role(request) == deps.ROLE_HR,
            "round_id": round_id,
            "page": page,
            "n_pages": len(competencies),
            "competency": None if done else competencies[page],
            "anchors": None if done else bank.anchors_for(competencies[page].id, banks.SELF),
            "perspective": banks.SELF,
            "scale": bank.scale["labels"],
            "answered": answered,
            "n_answered": len(answered),
            "n_items": len(bank.all_items),
            "done": done,
            "pages": pages,
            "prev_page": page - 1 if page > 0 else None,
            "next_page": page + 1 if page + 1 <= len(competencies) else None,
        },
    )


@router.post("/{token}", response_class=HTMLResponse)
async def save(request: Request, token: str, page: int = Form(0),
               round_id: str = Form(DEFAULT_ROUND), goto: int | None = Form(None)):
    """Save this page, then go where the button asked.

    `goto` is what makes Back and the jump list non-destructive: every way out of
    a page writes what is on it first, so navigating can never be the thing that
    loses an answer.
    """
    subject_id = rounds.subject_for_self_token(token, round_id)
    if subject_id is None:
        return _not_found(request)
    form_data = await request.form()
    values = {
        key[len("item_"):]: int(value)
        for key, value in form_data.items()
        if key.startswith("item_") and str(value).isdigit() and int(value) in (1, 2, 3)
    }
    if values:
        store.save_self_assessment(subject_id, round_id, values)

    target = page + 1 if goto is None else goto
    return RedirectResponse(f"/self/{token}?page={max(0, target)}", status_code=303)
