"""Adding a subject to the round.

The brief's administrator manages the instrument; `inputs/README.md` calls
`subjects.csv` the one file that must be edited before a round. This is that
edit, done from the browser, so a round can be started from scratch without
leaving the app.

Two things happen besides writing the row, and both are the point:

* the roster cache is cleared and the redactor rebuilt, so free text naming the
  new person is stripped from the first answer onward rather than from the next
  server restart onward;
* the subject is upserted into `responses.db`, because `subject` is what the
  reports join against and a roster row alone would render an empty round.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from poc import roster, store
from poc.graph import nodes
from poc.session import DEFAULT_ROUND

from .. import deps

router = APIRouter(prefix="/admin", dependencies=[Depends(deps.require_hr)])


def _known_subject_ids() -> set[str]:
    """Ids the store already knows, so a new one cannot collide with old ballots."""
    store.init()
    with store.connect() as conn:
        return {r["subject_id"] for r in conn.execute("SELECT subject_id FROM subject")}


@router.post("/subjects")
def create(request: Request, display_name: str = Form(...), role: str = Form(""),
           cohort: str = Form(""), round_id: str = Form(DEFAULT_ROUND)):
    try:
        subject = roster.add_subject(
            display_name=display_name,
            role=role,
            cohort=cohort,
            reserved=_known_subject_ids(),
        )
    except ValueError as exc:
        return RedirectResponse(f"/admin?error={exc}", status_code=303)

    # Order matters only in that both must happen before anyone answers a
    # question about this person. The redactor rebuild is the one that would fail
    # silently: nothing errors, the name simply survives into the store.
    nodes.refresh_redactor()
    store.upsert_subject(subject.subject_id, subject.display_name,
                         subject.cohort, subject.role)

    return RedirectResponse(f"/admin?added={subject.subject_id}", status_code=303)
