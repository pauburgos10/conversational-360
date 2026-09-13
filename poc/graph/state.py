"""Conversation state.

`thread_id` for the checkpointer is the ballot_id, which is what makes
save-and-resume fall out of LangGraph rather than needing its own machinery:
the rater's link IS the resume handle, and no login is involved.

Note what the state does NOT hold: any rater identity. It does hold raw answer
text — masking moved to render time (see `poc.reporting`), so the checkpoint now
carries the same words the response store does, and neither carries a name.
"""

from __future__ import annotations

from typing import Any, TypedDict

STAGE_CONSENT = "consent"
STAGE_LIKERT = "likert"
STAGE_PROBES = "probes"
STAGE_QUALITATIVE = "qualitative"
STAGE_REVIEW = "review"
STAGE_DONE = "done"
STAGE_DECLINED = "declined"
STAGE_CRISIS = "crisis"

KIND_CONSENT = "consent"
KIND_LIKERT = "likert"
KIND_OPEN = "open"
KIND_REVIEW = "review"


class InterviewState(TypedDict, total=False):
    # identity of the ballot only — never of the rater
    ballot_id: str
    subject_id: str
    subject_display: str
    round_id: str

    stage: str
    pending: dict[str, Any] | None   # the exact question awaiting an answer
    notice: str | None               # guardrail refusal or input-validation message

    consent: bool
    likert: dict[str, int]

    # Items the rater marked "not observed": they have no basis to rate them.
    # Kept OUT of `likert` on purpose — a decline is not a score, and anything
    # that merged the two would have to invent a number to merge it as. Carried
    # separately so the Likert walk can advance past them, the probe planner can
    # notice them, and the report can say how many raters could not assess a
    # competency, which is a finding in itself.
    declined: list[str]

    # Refs of optional probes the rater answered with "nothing to add". Needed
    # for the same reason `declined` is: the probe stage decides what to ask next
    # by looking at what has been answered, and an optional probe that stores no
    # answer would otherwise be asked forever.
    probes_skipped: list[str]

    probe_plan: list[dict[str, str]]
    probe_idx: int
    qual_idx: int

    followup_gap: str | None
    followups_current: int
    followups_total: int

    open_answers: list[dict[str, Any]]   # redacted text only
    guardrail_codes: list[str]           # codes only, never content
    redaction_counts: dict[str, int]

    transcript: list[dict[str, str]]
    halted: bool
    submitted: bool
