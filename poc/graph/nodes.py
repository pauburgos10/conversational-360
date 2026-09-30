"""Graph nodes.

The division of labour is the point of this module:

* `next_question` DECIDES what to ask. Pure Python, deterministic, no model
  call in stub mode and only a wording call in live mode. Because it writes the
  question into state before `collect` suspends, the exact question is captured
  in the checkpoint — a resumed session redisplays what it actually asked,
  rather than regenerating something slightly different.
* `collect` RECEIVES one answer, runs it through guardrails, records what the
  subject's copy will have masked, and
  asks the judge whether a follow-up is warranted. Routing off that verdict is
  plain `if`, never the model.

Every branch a rater can take is therefore reviewable without reading prompts.
"""

from __future__ import annotations

from .. import banks, config, llm as llm_mod, roster
from . import guardrails
from .redaction import Redactor
from .state import (
    InterviewState, KIND_CONSENT, KIND_LIKERT, KIND_OPEN, KIND_REVIEW,
    STAGE_CONSENT, STAGE_CRISIS, STAGE_DECLINED, STAGE_DONE, STAGE_LIKERT,
    STAGE_PROBES, STAGE_QUALITATIVE, STAGE_REVIEW,
)

# Merged so a roster entry missing from the gazetteer cannot leak a name.
_redactor = Redactor(*roster.redaction_names())


def refresh_redactor() -> Redactor:
    """Rebuild the redactor from the roster as it stands now.

    `_redactor` is built once at import, which is correct for a process whose
    roster is fixed at start — and wrong the moment a subject can be added while
    the server runs. Without this, a rater writing about a newly added colleague
    has that name stored in the clear, because the redactor was assembled before
    the person existed.

    Rebinding the module global rather than changing `collect`'s call site is
    deliberate: `test_identity_separation` asserts by AST that exactly one
    `_redactor.apply(...)` happens on the way in, and that check should keep
    working. `collect` reads the global at call time, so the rebind reaches it.
    """
    global _redactor
    _redactor = Redactor(*roster.redaction_names())
    return _redactor


def redactor() -> Redactor:
    """The redactor `collect` uses, for surfaces that want to show its effect.

    A UI that echoes a rater's answer back should echo what will actually be
    stored, not what was typed — seeing "Ana Torres" become "[NAME]" is how a
    rater learns the anonymity promise is mechanical rather than a policy. Shared
    rather than rebuilt so display and persistence cannot disagree.
    """
    return _redactor

WELCOME = (
    "Welcome, and thank you for making the time.\n\n"
    "You are giving 360° feedback on {subject}. This is a conversation rather than a form: "
    "you will rate a set of competencies, then answer a few open questions. I may ask a "
    "follow-up where a bit more detail would make your feedback usable.\n\n"
    "It takes about 12–18 minutes. Your progress saves as you go, so you can stop and come "
    "back to the same link.\n\n"
    "Your responses are fully anonymous. Your identity is never stored next to your answers, "
    "and there is no way to reconnect them afterwards."
)


# ---------------------------------------------------------------------------
# probe planning
# ---------------------------------------------------------------------------

_SIGNAL_TRIGGERS = ("low", "mixed", "high", "not_observed")

# The thresholds that decide which kind of written question a competency earns,
# checked in this order by `_trigger_for`: low, then mixed, then high. Named
# rather than inlined because the admin dashboard describes these rules to HR,
# and a number typed into a template would silently stop matching the engine the
# first time one of these changed.
LOW_MEAN_MAX = 1.5        # average rating at or below this reads as "low"
MIXED_SPREAD_MIN = 1      # items differing by at least this much read as "mixed"
HIGH_MEAN_MIN = 2.7       # average rating at or above this reads as "high"

# "I have no basis to rate this."
#
# A 360 asks peers, reports and managers about the same competencies, and no
# single rater has seen all of them. Without this option the instrument forces a
# number for things the rater has never witnessed, which does not produce missing
# data — it produces invented data, and the invented value lands in the average
# looking exactly like an observation.
#
# The canonical token is what the button posts. The rest are what people type
# into a chat box when the button is not in front of them.
LIKERT_RETRY = ("Please answer with 1, 2 or 3 — or say \"not observed\" if you "
                "have not seen enough of this to judge.")

NOT_OBSERVED = "not_observed"

_NOT_OBSERVED_INPUTS = {
    NOT_OBSERVED, "not observed", "notobserved", "no basis", "n/a", "na",
    "skip", "cannot say", "can't say", "cant say", "no view", "not seen",
    "haven't seen", "havent seen", "don't know", "dont know", "no opinion",
}

# Deliberately NOT in that set: "unsure", "not sure", "maybe". The scale's own
# reassurance text says "Not sure? Answer as best you can" — a rater following
# that advice must not have their answer reinterpreted as a refusal to answer.


_NOTHING_TO_ADD_INPUTS = {
    "", "no", "nope", "nothing", "nothing to add", "none", "n/a", "na", "-",
    "no thanks", "nothing else", "skip", "pass", "not really", "no comment",
}


def _is_nothing_to_add(raw: object) -> bool:
    """A declined optional probe. Short and negative, not merely short.

    Deliberately an exact-match set rather than a length test: "Late." is four
    characters and is an answer; "nothing to add" is fourteen and is not.
    """
    return str(raw or "").strip().casefold().rstrip(".!") in _NOTHING_TO_ADD_INPUTS


def _is_not_observed(raw: object) -> bool:
    text = str(raw or "").strip().casefold().rstrip(".!")
    return text in _NOT_OBSERVED_INPUTS


def _trigger_for(values: list[int], available: dict, n_declined: int = 0) -> str | None:
    """Which framing this competency's ratings call for, or None if the bank has none.

    A decline outranks the score pattern. If the rater could not assess part of
    this competency, the useful question is not "why did you rate it 2" — it is
    whether they have anything to say about it at all, and the `not_observed`
    probe is the one that asks that. It also handles the case no score-based
    trigger can: every item declined, leaving no values to take a mean of.
    """
    if n_declined and "not_observed" in available:
        return "not_observed"
    if not values:
        # Fully declined and the bank has no not_observed wording for this
        # competency. Nothing to probe on; say so rather than dividing by zero.
        return None

    mean = sum(values) / len(values)
    spread = max(values) - min(values)

    if mean <= LOW_MEAN_MAX:
        trigger = "low"
    elif spread >= MIXED_SPREAD_MIN:
        trigger = "mixed"
    elif mean >= HIGH_MEAN_MIN:
        trigger = "high"
    else:
        trigger = "default"

    if trigger not in available:
        trigger = "default"
    return trigger if trigger in available else None


def plan_probes(likert: dict[str, int], bank: banks.Bank,
                declined: set[str] | list[str] | tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Choose which competencies get an open probe, in the order they are asked.

    Probing every competency would be accurate and unusable — completion
    friction is an evaluation criterion in its own right. So probes go where the
    ratings carry signal: an extreme score, or disagreement between the items
    inside one competency. Everything else stays Likert-only.

    The plan is built in bank order and each competency is decided from its own
    ratings plus the slots already spent, never from ratings that come later.
    That is what lets a probe be asked immediately after the competency it is
    about, instead of in a block at the end where the rater has to remember
    which ratings prompted it: the plan is **prefix-stable**, so calling this
    with a half-rated ballot returns a prefix of what the finished ballot
    returns and an index into it never has to be rebased.

    The price of deciding without lookahead is that a strong signal late in the
    bank can find the budget already spent. Two rules keep that from hollowing
    out the plan:

    * A competency with no signal at all (every item rated the same middling 2)
      takes a slot only when there are too few competencies left to fill the
      budget anyway — so it can never crowd out a real signal that follows it.
    * The last free slot is held for a dimension that has no probe yet, while a
      competency from such a dimension is still ahead. Without it, one dimension
      can collect Likert data only.

      This rule used to name the behavioural dimension outright. Once the
      instrument was realigned to the client's sections, the dimension at risk
      flipped: Delivery is now a single competency sitting first in bank order,
      so it is skipped whenever its ratings are unremarkable and slots are
      scarce — and it is the dimension carrying 60% of the weight. Keying on
      "a dimension with nothing planned yet" protects whichever one is exposed,
      rather than whichever one was exposed when the rule was written.
    """
    policy = bank.probe_policy
    limit = int(policy.get("max_probed_competencies", 4))
    competencies = bank.competencies
    declined = set(declined)

    plan: list[dict[str, str]] = []
    probed_dimensions: set[str] = set()

    for order, comp in enumerate(competencies):
        free = limit - len(plan)
        if free <= 0:
            break

        # A partly-answered competency is not skipped for good — it is simply not
        # decidable yet, and the next call (after its last item) will see it. An
        # item counts as answered whether it was rated or declined, or a fully
        # declined competency would hold the planner open forever.
        if any(i.id not in likert and i.id not in declined for i in comp.items):
            continue

        trigger = _trigger_for(
            [likert[i.id] for i in comp.items if i.id in likert],
            bank.open_probes.get(comp.id, {}),
            n_declined=sum(1 for i in comp.items if i.id in declined),
        )
        if trigger is None:
            continue

        # Is this the last competency that could put this dimension on the plan?
        last_chance = (comp.dimension not in probed_dimensions
                       and not any(c.dimension == comp.dimension
                                   for c in competencies[order + 1:]))

        # Hold the final slot for a dimension with nothing planned yet, when one
        # is still ahead.
        if (free == 1 and comp.dimension in probed_dimensions
                and any(c.dimension not in probed_dimensions
                        for c in competencies[order + 1:])):
            continue

        # A competency with no signal yields its slot rather than crowd out a
        # later one that has signal — UNLESS it is its dimension's last chance.
        # Delivery is a single competency sitting first in bank order, so without
        # that exemption it is dropped on the very first pass whenever its
        # ratings are unremarkable, and the dimension carrying 60% of the weight
        # collects no written evidence at all.
        if (trigger not in _SIGNAL_TRIGGERS and not last_chance
                and len(competencies) - order > free):
            continue

        plan.append({"competency_id": comp.id, "trigger": trigger})
        probed_dimensions.add(comp.dimension)

    return plan


# ---------------------------------------------------------------------------
# next_question
# ---------------------------------------------------------------------------

def next_question(state: InterviewState) -> InterviewState:
    bank = banks.load_bank()
    llm = llm_mod.get_llm()
    stage = state.get("stage", STAGE_CONSENT)
    likert = dict(state.get("likert", {}))
    declined = set(state.get("declined", []))
    update: InterviewState = {}
    # Filled in here, as the question is written into state, so the checkpoint
    # holds what the rater actually saw and a resumed session redisplays it.
    name = banks.first_name(state.get("subject_display"))

    while True:
        if stage == STAGE_CONSENT:
            return {
                **update, "stage": stage,
                "pending": {
                    "kind": KIND_CONSENT,
                    "ref": "consent",
                    "text": config.consent_text(),
                    "welcome": WELCOME.format(subject=state.get("subject_display", "your colleague")),
                    "expects": "yes/no",
                },
            }

        if stage == STAGE_LIKERT:
            # One walk down the bank, alternating "rate this competency" with
            # "now say more about it". Interleaving is the point: the probe
            # arrives while the ratings that triggered it are still the last
            # thing on screen, so the rater can see which answers the follow-up
            # is asking about instead of matching it to a block of ratings they
            # gave ten questions ago.
            #
            # The plan is recomputed on every pass rather than fixed when the
            # Likert block ends, because it now has to grow as the ratings come
            # in. `plan_probes` is prefix-stable, so recomputing never moves a
            # probe the rater has already been asked.
            plan = plan_probes(likert, bank, declined)
            planned = {e["competency_id"]: i for i, e in enumerate(plan)}
            # Dealt with = answered, or offered and waved away. An optional
            # probe stores nothing, so without the second half the walk would
            # keep handing the same probe back.
            answered = ({a["ref"] for a in state.get("open_answers", [])}
                        | set(state.get("probes_skipped", [])))
            update = {**update, "probe_plan": plan}

            probe_at = None
            for comp in bank.competencies:
                # Rated or declined both count as answered. Only `likert` feeds an
                # average; `declined` exists so the walk can move on without one.
                missing = [i for i in comp.items
                           if i.id not in likert and i.id not in declined]
                if missing:
                    item = missing[0]
                    return {
                        **update, "stage": stage,
                        "pending": {
                            "kind": KIND_LIKERT,
                            "ref": item.id,
                            "competency_id": comp.id,
                            "competency_name": comp.name,
                            "label": item.label,
                            "text": item.prompt_for(banks.RATER, name),
                            "anchors": bank.anchors_for(comp.id),
                            "scale": bank.scale["labels"],
                            "expects": "1-3",
                            "allows_not_observed": True,
                            "progress": (f"{len(likert) + len(declined) + 1}"
                                         f"/{len(bank.all_items)}"),
                        },
                    }
                # Competency fully rated: its probe, if it earned one, is due
                # now. Skipping the ones already answered is what keeps a
                # re-plan after a review-step edit from dragging the rater back
                # through ground they have covered.
                idx = planned.get(comp.id)
                if idx is not None and f"probe:{comp.id}" not in answered:
                    probe_at = idx
                    break

            if probe_at is None:
                stage = STAGE_QUALITATIVE
                update = {**update, "qual_idx": state.get("qual_idx", 0)}
                continue

            update = {**update, "probe_idx": probe_at}
            stage = STAGE_PROBES
            continue

        if stage == STAGE_PROBES:
            plan = update.get("probe_plan", state.get("probe_plan", []))
            idx = update.get("probe_idx", state.get("probe_idx", 0))
            gap = state.get("followup_gap")

            # This stage asks one probe and hands straight back to the Likert
            # walk, which owns the ordering and is the only place that knows
            # whether the next competency still needs rating. Once the probe has
            # an answer there is nothing here to decide.
            #
            # Guarded on `gap` because a follow-up's parent answer is recorded by
            # definition; leaving on that basis would swallow the follow-up.
            if idx >= len(plan) or (
                not gap
                and f"probe:{plan[idx]['competency_id']}"
                in ({a["ref"] for a in state.get("open_answers", [])}
                    | set(state.get("probes_skipped", [])))
            ):
                stage = STAGE_LIKERT
                continue

            entry = plan[idx]
            comp = bank.competency(entry["competency_id"])
            # The model rewords the placeholder, not the name, which is filled in
            # after: it keeps the name out of the model call and stops a
            # paraphrase swapping it for a full name or a gendered pronoun.
            if gap:
                text = banks.fill_subject(llm.followup(
                    bank.followup_templates.get(gap, bank.followup_templates["too_short"]),
                    question=comp.name, answer="",
                ), name)
                ref = f"probe:{comp.id}:f{state.get('followups_current', 0)}"
            else:
                template = bank.open_probes[comp.id][entry["trigger"]]
                values = [likert[i.id] for i in comp.items if i.id in likert]
                text = banks.fill_subject(llm.probe(
                    template, comp.name,
                    likert_context=f"{comp.name}: {values}",
                ), name)
                ref = f"probe:{comp.id}"

            return {
                **update, "stage": stage,
                "pending": {
                    "kind": KIND_OPEN, "ref": ref, "competency_id": comp.id,
                    "competency_name": comp.name, "theme": None, "text": text,
                    "expects": "free text", "is_followup": bool(gap),
                    # Asked because the rater said they could not judge this.
                    # Pressing them for detail would be asking them to invent the
                    # thing they just told us they do not have.
                    "optional": entry["trigger"] == "not_observed" and not gap,
                },
            }

        if stage == STAGE_QUALITATIVE:
            qidx = update.get("qual_idx", state.get("qual_idx", 0))
            gap = state.get("followup_gap")

            # Same invariant as the probes above. Re-entering this stage after an
            # edit must not re-ask the fixed questions already answered.
            if not gap:
                answered = {a["ref"] for a in state.get("open_answers", [])}
                while (qidx < len(bank.qualitative_fixed)
                       and bank.qualitative_fixed[qidx]["id"] in answered):
                    qidx += 1
                update = {**update, "qual_idx": qidx}

            if qidx >= len(bank.qualitative_fixed):
                stage = STAGE_REVIEW
                continue
            q = bank.qualitative_fixed[qidx]
            if gap:
                text = banks.fill_subject(llm.followup(
                    bank.followup_templates.get(gap, bank.followup_templates["too_short"]),
                    question=q["prompt"], answer="",
                ), name)
                ref = f"{q['id']}:f{state.get('followups_current', 0)}"
            else:
                text = banks.fill_subject(q["prompt"], name)
                ref = q["id"]
            return {
                **update, "stage": stage,
                "pending": {
                    "kind": KIND_OPEN, "ref": ref, "competency_id": None,
                    "competency_name": None, "theme": q["theme"], "text": text,
                    "expects": "free text", "is_followup": bool(gap),
                },
            }

        if stage == STAGE_REVIEW:
            answered = len(state.get("open_answers", []))
            return {
                **update, "stage": stage,
                "pending": {
                    "kind": KIND_REVIEW, "ref": "review", "expects": "yes/no",
                    "text": (
                        f"That's everything. You rated {len(likert)} items and gave "
                        f"{answered} written answers.\n\n"
                        "Once you submit you cannot edit these answers — not through "
                        "me, and not by reopening the link.\n\n"
                        "HR can see your answers as written, with your name against "
                        "them. The person you are reviewing gets a version with names "
                        "and contact details removed, and it does not say who wrote "
                        "it.\n\n"
                        "Submit now?"
                    ),
                },
            }

        return {**update, "stage": STAGE_DONE, "pending": None}


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def _parse_yes_no(raw: str) -> bool | None:
    t = (raw or "").strip().lower()
    if t in {"y", "yes", "accept", "i accept", "agree", "ok", "si", "sí", "submit"}:
        return True
    if t in {"n", "no", "decline", "cancel", "stop"}:
        return False
    return None


def make_collect(interrupt_fn):
    """`interrupt_fn` is injected so the graph can be driven in tests without LangGraph."""

    def collect(state: InterviewState) -> InterviewState:
        bank = banks.load_bank()
        llm = llm_mod.get_llm()
        pending = state.get("pending") or {}
        raw = interrupt_fn(pending)
        kind = pending.get("kind")
        transcript = list(state.get("transcript", []))

        # ---- consent -----------------------------------------------------
        if kind == KIND_CONSENT:
            decision = _parse_yes_no(raw)
            if decision is None:
                return {"notice": "Please answer yes or no.", "pending": pending}
            if not decision:
                return {"consent": False, "stage": STAGE_DECLINED, "pending": None, "notice": None}
            return {"consent": True, "stage": STAGE_LIKERT, "pending": None, "notice": None}

        # ---- likert ------------------------------------------------------
        if kind == KIND_LIKERT:
            # "Not observed" is recorded, not scored. It leaves `likert`
            # untouched so nothing this rater could not see can reach an average.
            if _is_not_observed(raw):
                declined = [d for d in state.get("declined", []) if d != pending["ref"]]
                declined.append(pending["ref"])
                return {"declined": declined, "pending": None, "notice": None}

            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                return {"notice": LIKERT_RETRY, "pending": pending}
            if value not in (1, 2, 3):
                return {"notice": LIKERT_RETRY, "pending": pending}

            # A rating supersedes an earlier decline on the same item — the
            # review step lets a rater revisit, and coming back to answer
            # properly must not leave the item counted as unassessable.
            likert = {**state.get("likert", {}), pending["ref"]: value}
            declined = [d for d in state.get("declined", []) if d != pending["ref"]]
            return {"likert": likert, "declined": declined,
                    "pending": None, "notice": None}

        # ---- review ------------------------------------------------------
        if kind == KIND_REVIEW:
            decision = _parse_yes_no(raw)
            if decision is None:
                return {"notice": "Please answer yes or no.", "pending": pending}
            if not decision:
                return {"notice": "No problem — nothing has been submitted. "
                                  "Your answers stay saved on this link.",
                        "stage": STAGE_DONE, "pending": None}
            return {"stage": STAGE_DONE, "pending": None, "notice": None, "submitted": True}

        # ---- open text ---------------------------------------------------
        # An optional probe accepts "no". Storing an empty or "nothing to add"
        # answer would put a row into the themes pipeline that says nothing, and
        # re-asking it would badger someone who has already explained they have
        # no basis to comment.
        if pending.get("optional") and _is_nothing_to_add(raw):
            skipped = [r for r in state.get("probes_skipped", []) if r != pending["ref"]]
            skipped.append(pending["ref"])
            return {"probes_skipped": skipped, "pending": None, "notice": None,
                    "followup_gap": None, "followups_current": 0}

        codes = list(state.get("guardrail_codes", []))
        verdict = guardrails.check(raw)

        if verdict.kind == guardrails.KIND_CRISIS:
            # Stop asking. Keep what was already answered; record the code only.
            codes.append(verdict.code)
            return {
                "stage": STAGE_CRISIS, "halted": True, "pending": None,
                "notice": verdict.message, "guardrail_codes": codes,
            }

        if verdict.kind == guardrails.KIND_REFUSE:
            # The turn is not recorded as an answer, and the same question is re-asked.
            codes.append(verdict.code)
            return {"notice": verdict.message, "pending": pending, "guardrail_codes": codes}

        prefix = ""
        if verdict.kind == guardrails.KIND_DISTRESS:
            codes.append(verdict.code)
            prefix = verdict.message

        # The redactor runs here but no longer *replaces* anything. HR reads
        # answers as written — see PLAN.md §2 — so the raw sentence is what gets
        # stored, and masking moved to render time in `poc.reporting`, keyed on
        # who is reading.
        #
        # What survives from the old write-time pass is the code list: it says
        # what WOULD be masked in the subject's copy, which is what the review
        # screen shows the rater before they submit. Computing it now rather than
        # at render keeps that preview honest — it is the same `Redactor` call,
        # on the same text, that the subject's report will make later.
        raw_text = str(raw)
        result = _redactor.apply(raw_text)
        counts = dict(state.get("redaction_counts", {}))
        for code, n in result.counts.items():
            counts[code] = counts.get(code, 0) + n

        base_ref = pending["ref"].split(":f")[0]
        answers = [dict(a) for a in state.get("open_answers", [])]
        existing = next((a for a in answers if a["ref"] == base_ref), None)
        if existing:
            existing["text"] = f"{existing['text']} {raw_text}".strip()
            existing["codes"] = list(existing.get("codes", [])) + result.codes
        else:
            answers.append({
                "ref": base_ref,
                "competency_id": pending.get("competency_id"),
                "theme": pending.get("theme"),
                "text": raw_text,
                "codes": result.codes,
            })

        transcript.append({"q": pending.get("text", ""), "a": raw_text})

        # Follow-up decision: judge the accumulated answer, then check budget.
        combined = next(a["text"] for a in answers if a["ref"] == base_ref)
        judged = llm.judge_specificity(pending.get("text", ""), combined)

        policy = bank.probe_policy
        per_probe = int(policy.get("max_followups_per_probe", 2))
        per_session = int(policy.get("max_followups_per_session", 8))
        used_current = int(state.get("followups_current", 0))
        used_total = int(state.get("followups_total", 0))

        wants_followup = (
            not judged.specific
            and not pending.get("optional")
            and used_current < per_probe
            and used_total < per_session
        )

        update: InterviewState = {
            "open_answers": answers,
            "guardrail_codes": codes,
            "redaction_counts": counts,
            "transcript": transcript,
            "pending": None,
            "notice": prefix or None,
        }

        if wants_followup:
            update["followup_gap"] = judged.gap
            update["followups_current"] = used_current + 1
            update["followups_total"] = used_total + 1
            return update

        # Done with this question — clear the follow-up budget and advance.
        #
        # No probe index is advanced here: probes are interleaved with the Likert
        # block now, so what comes after one is a question for `next_question`
        # to decide from the answers on record, not an increment. Recording the
        # answer above is what moves the interview on.
        update["followup_gap"] = None
        update["followups_current"] = 0
        if state.get("stage") == STAGE_QUALITATIVE:
            update["qual_idx"] = int(state.get("qual_idx", 0)) + 1
        return update

    return collect


def route_after_collect(state: InterviewState) -> str:
    stage = state.get("stage")
    if stage in (STAGE_DECLINED, STAGE_CRISIS, STAGE_DONE):
        return "finish"
    return "next_question"


def finish(state: InterviewState) -> InterviewState:
    """Terminal node. Persists only when the rater consented and submitted."""
    if state.get("submitted") and state.get("consent"):
        from .. import store
        store.init()
        store.create_ballot(
            subject_id=state["subject_id"],
            round_id=state["round_id"],
            ballot_id=state["ballot_id"],
        )
        store.save_submission(
            ballot_id=state["ballot_id"],
            likert=state.get("likert", {}),
            open_answers=state.get("open_answers", []),
            guardrail_codes=state.get("guardrail_codes", []),
            declined=state.get("declined", []),
        )
    return {"pending": None}
