"""Driving one interview, without knowing what is rendering it.

`cli_interview` used to own this loop. Once a web UI needed the same thing, the
loop had to move somewhere both could import, or the two surfaces would drift:
consent parsing, the notice channel and the "which question are we on" bookkeeping
are exactly the details that get re-implemented slightly differently the second time.

The API is deliberately small, because the graph already does the hard part:

    turn = Interview(ballot_id, subject_id, ...).open()   # start or resume
    turn = interview.send("2")                            # one rater reply

Everything a surface needs to render is on `Turn`. Nothing on it is engine state
a caller could corrupt — `pending` is the checkpointed question dict, copied out.

The one thing worth knowing: `open()` is idempotent. Calling it on a ballot that
is mid-interview re-displays the question the checkpoint is holding rather than
advancing, which is what makes an HTTP GET on the resume URL safe to repeat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.types import Command

from . import banks, store
from .graph import nodes
from .graph.build import build_app
from .graph.state import (
    STAGE_CRISIS, STAGE_DECLINED, STAGE_LIKERT, STAGE_PROBES, STAGE_QUALITATIVE,
    STAGE_REVIEW,
)

DEFAULT_ROUND = "2026-H2"

# Stages an edit can arrive in at all. Not sufficient on its own: probes are
# interleaved with the Likert block, so STAGE_PROBES no longer means the ratings
# are all in — see `_guard_editable`.
EDITABLE_STAGES = (STAGE_PROBES, STAGE_QUALITATIVE, STAGE_REVIEW)


class EditRefused(Exception):
    """An edit that must not be applied, with a reason fit to show a rater."""


@dataclass(frozen=True)
class Turn:
    """One render-able moment in the conversation.

    `pending is None` means the interview is over; `outcome` then says how it
    ended. While `pending` is set, `notice` carries anything the engine wants
    said *alongside* the question — a guardrail refusal, a validation nudge, a
    distress acknowledgement.
    """

    pending: dict[str, Any] | None
    notice: str | None = None
    stage: str = ""
    resumed: bool = False
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.pending is None

    @property
    def submitted(self) -> bool:
        return bool(self.values.get("submitted"))

    @property
    def outcome(self) -> str:
        """declined | crisis | submitted | saved | in_progress"""
        if not self.finished:
            return "in_progress"
        if self.stage == STAGE_DECLINED:
            return "declined"
        if self.stage == STAGE_CRISIS:
            return "crisis"
        return "submitted" if self.submitted else "saved"

    @property
    def summary(self) -> dict[str, Any]:
        """Counts for the closing screen. Empty until the interview ends."""
        if not self.finished:
            return {}
        return {
            "likert": len(self.values.get("likert", {})),
            "open": len(self.values.get("open_answers", [])),
            "followups": self.values.get("followups_total", 0),
            "redactions": self.values.get("redaction_counts", {}),
        }


class Interview:
    """One ballot's conversation. `ballot_id` is the checkpoint thread id."""

    def __init__(self, ballot_id: str, subject_id: str = "S1",
                 round_id: str = DEFAULT_ROUND, checkpoint_path: Path | None = None):
        self.ballot_id = ballot_id
        self.subject_id = subject_id
        self.round_id = round_id
        self._app = build_app(checkpoint_path)
        self._cfg = {"configurable": {"thread_id": ballot_id}}

    # -- lifecycle ---------------------------------------------------------

    @property
    def exists(self) -> bool:
        return bool(self._app.get_state(self._cfg).values)

    @property
    def saved_subject_id(self) -> str | None:
        """Who an existing conversation is about, as recorded when it started.

        The checkpoint is the authority once a ballot exists. The `subject_id`
        passed to the constructor only decides who a *new* conversation is about,
        and a caller that guessed it — from a link with no `?subject=` — would
        otherwise head someone else's review with the wrong name.
        """
        return self._app.get_state(self._cfg).values.get("subject_id") or None

    def open(self) -> Turn:
        """Start a new interview, or re-present the question a resumed one holds."""
        if self.exists:
            return self._turn(self._app.invoke(None, self._cfg), resumed=True)
        return self._turn(
            self._app.invoke(
                {
                    "ballot_id": self.ballot_id,
                    "subject_id": self.subject_id,
                    "subject_display": subject_display_name(self.subject_id),
                    "round_id": self.round_id,
                    "stage": "consent",
                },
                self._cfg,
            )
        )

    def send(self, reply: str) -> Turn:
        return self._turn(self._app.invoke(Command(resume=reply), self._cfg))

    # -- editing an earlier answer ----------------------------------------
    #
    # Both edits patch the checkpoint and then let the graph run itself forward,
    # rather than reaching into the conversation. `as_node="collect"` is what
    # makes that work: it tells the checkpointer the patch arrived the way any
    # answer does, so the next invoke follows `collect`'s own edges into
    # `next_question` and the flow decides what to ask next. Nothing here
    # duplicates routing logic that lives in the graph.

    def _guard_editable(self, state: dict) -> None:
        if state.get("submitted"):
            raise EditRefused(
                "This ballot has been submitted and cannot be changed."
            )
        if state.get("halted") or state.get("stage") not in EDITABLE_STAGES:
            raise EditRefused("There is nothing to change yet.")
        # An interleaved probe puts the rater in STAGE_PROBES while competencies
        # further down the bank are still unanswered. Re-planning against a
        # half-answered ballot would decide probes for ratings that do not exist
        # yet, so an edit waits until the whole battery is done — which is also
        # the only point the UI offers one.
        #
        # "Done" counts declines as well as ratings. Counting `likert` alone made
        # every ballot containing a single "not observed" permanently uneditable,
        # because that item can never appear there — which silently removed the
        # review step from exactly the raters most likely to want it.
        answered = len(state.get("likert", {})) + len(state.get("declined", []))
        if answered < len(banks.load_bank().all_items):
            raise EditRefused("There is nothing to change yet.")

    def edit_rating(self, item_id: str, value: int) -> Turn:
        """Change one Likert answer, then re-plan which competencies get probed.

        Re-planning is the point rather than a side effect: probes go where the
        ratings carry signal, so a rating that moves from 2 to 1 can make a
        competency the most important one to ask about. Leaving the old plan
        would mean the written evidence answers a question the ratings no longer
        pose.

        The cost is real and deliberate — a rater at the review step can be sent
        back into the interview for a probe that did not exist a moment ago.
        `next_question` skips anything already answered, so the return trip only
        covers genuinely new ground.
        """
        state = self._app.get_state(self._cfg).values
        self._guard_editable(state)

        try:
            value = int(value)
        except (TypeError, ValueError):
            raise EditRefused("A rating must be 1, 2 or 3.") from None
        if value not in (1, 2, 3):
            raise EditRefused("A rating must be 1, 2 or 3.")

        likert = dict(state.get("likert", {}))
        declined = [d for d in state.get("declined", []) if d != item_id]
        was_declined = len(declined) != len(state.get("declined", []))

        if item_id not in likert and not was_declined:
            raise EditRefused("That question has not been answered yet.")
        if likert.get(item_id) == value:
            return self._turn(self._app.invoke(None, self._cfg))

        # Editing is also how a rater changes their mind about "not observed" —
        # they remember an example, and the item becomes ratable. Dropping it
        # from `declined` here is what stops it being counted as unassessable
        # after it has been assessed.
        likert[item_id] = value
        plan = nodes.plan_probes(likert, banks.load_bank(), declined)

        self._app.update_state(
            self._cfg,
            {
                "likert": likert,
                "declined": declined,
                "probe_plan": plan,
                # Hand back to the walk at the top of the bank and let it skip
                # what is answered, rather than computing a resume point here —
                # one place decides what has been covered. With the battery
                # complete the walk has no rating left to ask for, so it lands on
                # the first probe the new plan added, or on the review.
                "probe_idx": 0,
                "stage": STAGE_LIKERT,
                "pending": None,
                "followup_gap": None,
                "followups_current": 0,
                "notice": None,
            },
            as_node="collect",
        )
        return self._turn(self._app.invoke(None, self._cfg))

    def edit_answer(self, ref: str, text: str) -> Turn:
        """Replace a written answer, redacted the same way the original was.

        Redaction runs through `nodes.redactor()` — the object `collect` uses —
        so an edited answer cannot reach the store under looser rules than the
        one it replaces.

        What the rater edits is the *stored* answer, which for a probe that drew
        a follow-up is the parent and its follow-ups merged into one text. That
        is what the report will quote, so it is the honest thing to show, even
        though it is not word-for-word the turn they remember typing.
        """
        state = self._app.get_state(self._cfg).values
        self._guard_editable(state)

        text = (text or "").strip()
        if not text:
            raise EditRefused("An answer cannot be emptied. Rewrite it instead.")

        answers = [dict(a) for a in state.get("open_answers", [])]
        target = next((a for a in answers if a["ref"] == ref), None)
        if target is None:
            raise EditRefused("That answer is not part of this ballot.")

        # Same split as `collect`: keep what they wrote, record what the subject's
        # copy will mask. Editing an answer must not quietly re-redact it.
        result = nodes.redactor().apply(text)
        target["text"] = text
        target["codes"] = list(result.codes)

        counts = dict(state.get("redaction_counts", {}))
        for code, n in result.counts.items():
            counts[code] = counts.get(code, 0) + n

        self._app.update_state(
            self._cfg,
            {"open_answers": answers, "redaction_counts": counts, "notice": None},
            as_node="collect",
        )
        return self._turn(self._app.invoke(None, self._cfg))

    def summary_for_review(self) -> dict:
        """Everything the review screen needs to show what will be submitted."""
        bank = banks.load_bank()
        state = self._app.get_state(self._cfg).values
        likert = state.get("likert", {})

        declined = set(state.get("declined", []))
        name = banks.first_name(state.get("subject_display"))

        competencies = []
        for comp in bank.competencies:
            # Keyed "questions", not "items": a template reading `comp.items`
            # gets the dict's own `.items` method, and the failure is a
            # TypeError deep in a loop rather than anything that names the cause.
            #
            # Declined items are listed here too. The review screen is the last
            # chance to change an answer, and an item the rater opted out of is
            # the most likely one to want back — they have since been asked about
            # the competency and may have remembered something.
            questions = [
                {"id": i.id, "label": i.label, "prompt": i.prompt_for(banks.RATER, name),
                 "value": likert.get(i.id), "declined": i.id in declined}
                for i in comp.items if i.id in likert or i.id in declined
            ]
            if questions:
                competencies.append({
                    "id": comp.id, "name": comp.name,
                    "anchors": bank.anchors_for(comp.id), "questions": questions,
                })

        labels = {q["id"]: banks.fill_subject(q["prompt"], name)
                  for q in bank.qualitative_fixed}
        answers = []
        for a in state.get("open_answers", []):
            ref = a["ref"]
            if ref.startswith("probe:"):
                heading = bank.competency(ref.split(":", 1)[1]).name
            else:
                heading = labels.get(ref, ref)
            answers.append({"ref": ref, "heading": heading, "text": a["text"],
                            "codes": a.get("codes", [])})

        return {
            "competencies": competencies,
            "answers": answers,
            "scale": bank.scale["labels"],
            "n_likert": len(likert),
            "n_answers": len(answers),
        }

    # -- internals ---------------------------------------------------------

    def _turn(self, out: dict, resumed: bool = False) -> Turn:
        values = self._app.get_state(self._cfg).values
        interrupts = out.get("__interrupt__")
        pending = dict(interrupts[0].value) if interrupts else None
        return Turn(
            pending=pending,
            notice=out.get("notice"),
            stage=values.get("stage", ""),
            resumed=resumed,
            values=values,
        )


def subject_display_name(subject_id: str) -> str:
    """Resolve a subject id against the store, failing with a usable message.

    Reads the store rather than `inputs/subjects.csv` on purpose: a subject that
    is in the roster file but was never seeded has no ballots to attach to, and
    discovering that at report time is far more confusing than here.
    """
    store.init()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT display_name FROM subject WHERE subject_id=?", (subject_id,)
        ).fetchone()
    if row is None:
        raise LookupError(
            f"Unknown subject {subject_id!r}. Run `python -m poc.seed_ballots` first, "
            "or pick one of the seeded subject ids (S1…S6)."
        )
    return row["display_name"]
