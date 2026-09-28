"""LLM adapter with a deterministic stub.

Two modes behind one interface:

* ``stub``   — no network, no key, no cost. Deterministic. Used by the tests and
               by anyone running the demo without credentials.
* ``openai`` — LangChain + structured output, used when OPENAI_API_KEY is set.

What the model is and is not allowed to do here is the important part:

* It **words** probes and follow-ups, drawing on templates from the question
  bank. It never invents a question that has no bank entry.
* It **judges** whether an answer is specific enough, returning a structured
  verdict. The routing decision off the back of that verdict is plain Python.
* It **clusters** open answers into themes. Its output is then checked for
  verbatim leakage by `contains_verbatim` regardless of mode — a model that is
  told to paraphrase and does not is caught by code, not by trust.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import banks, config, trace

# --------------------------------------------------------------------------
# Structured verdicts
# --------------------------------------------------------------------------

GAP_TOO_SHORT = "too_short"
GAP_NEEDS_EXAMPLE = "needs_example"
GAP_NEEDS_BEHAVIOUR = "needs_behaviour"
GAP_NEEDS_EFFECT = "needs_effect"


@dataclass(frozen=True)
class SpecificityVerdict:
    specific: bool
    gap: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Theme:
    id: str
    label: str
    paraphrase: str
    valence: str            # strength | opportunity | neutral
    ballot_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n(self) -> int:
        return len(set(self.ballot_ids))


# --------------------------------------------------------------------------
# Heuristics shared by both modes
# --------------------------------------------------------------------------

_CONCRETE = re.compile(
    r"\b\d+\b|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b"
    r"|\b(?:last|this|next)\s+(?:week|month|quarter|sprint|year)\b"
    r"|\b(?:when|once|during|after|before|example|instance|case|time that|the time)\b"
    r"|\bq[1-4]\b",
    re.I,
)
# Outcome markers. Kept broad on purpose: a false negative here costs the rater
# an unnecessary follow-up on an answer that was already good, which is the
# fastest way to make a guided conversation feel like an interrogation.
_EFFECT = re.compile(
    r"\b(?:so that|so we|so the|as a result|resulted in|result was|led to|meant that|ended up"
    r"|because of that|the impact|the effect|otherwise|allowed us|freed up|saved us|thanks to that"
    r"|which (?:meant|saved|avoided|cut|reduced|freed|allowed|let|helped|prevented|stopped|gave))\b",
    re.I,
)
_STATIVE_ONLY = re.compile(r"^(?:\W|\b(?:is|are|was|were|has|have|had|be|been|very|really|quite|always|never|good|great|fine|ok|okay|bad|poor|nice|solid|strong|weak|they|he|she|them|a|an|the|and|but|to|of|in|on|at|it|very)\b|\s)*$", re.I)

# Length caps on what the MODEL writes — not on what a rater writes.
#
# These bound the model's own output inside the prompts below, and are
# deliberately independent of `probe_policy.min_words`, which is the floor on a
# rater's answer. The two used to share the literal 12 by coincidence, which read
# as if the judge's explanation were tied to the configurable answer floor. It
# is not, and must not be: raising min_words to demand longer answers would then
# also make the model write longer justifications, which is meaningless.
JUDGE_REASON_MAX_WORDS = 12
PROBE_MAX_WORDS = 40
FOLLOWUP_MAX_WORDS = 35

# Fallback only. The live value is questions.json `probe_policy.min_words`: it
# sits with the other session-length levers because raising it buys depth by
# spending follow-ups, which is the same trade max_followups_per_probe makes.
DEFAULT_MIN_WORDS = 12


def min_words() -> int:
    """Word floor below which an answer is `too_short`, from the question bank."""
    try:
        configured = banks.load_bank().probe_policy.get("min_words")
    except Exception:
        return DEFAULT_MIN_WORDS
    return DEFAULT_MIN_WORDS if configured is None else int(configured)


def heuristic_specificity(answer: str, floor: int | None = None) -> SpecificityVerdict:
    """Deterministic pre-filter, run in both modes before any model call.

    Ordering matters: asking for an effect before there is even an example
    produces a confusing follow-up, so gaps are returned in escalating order.
    """
    text = (answer or "").strip()
    words = text.split()
    floor = min_words() if floor is None else floor

    if len(words) < floor:
        return SpecificityVerdict(False, GAP_TOO_SHORT, f"{len(words)} words")
    if _STATIVE_ONLY.match(text):
        return SpecificityVerdict(False, GAP_NEEDS_BEHAVIOUR, "no described action")
    if not _CONCRETE.search(text):
        return SpecificityVerdict(False, GAP_NEEDS_EXAMPLE, "no time, count or occasion")
    if not _EFFECT.search(text):
        return SpecificityVerdict(False, GAP_NEEDS_EFFECT, "outcome not stated")
    return SpecificityVerdict(True, None, "grounded and has an outcome")


def _normalise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def contains_verbatim(candidate: str, sources: list[str], n: int = 6) -> bool:
    """True when `candidate` shares an n-gram of `n` words with any source.

    This is the enforcement behind reporting-rules.yaml `allow_verbatim_quotes:
    false`. A verbatim sentence from a single rater is re-identifying even after
    names are stripped — voice and specifics give the author away.
    """
    cand = _normalise(candidate)
    if len(cand) < n:
        return False
    cand_grams = {tuple(cand[i:i + n]) for i in range(len(cand) - n + 1)}
    for source in sources:
        src = _normalise(source)
        for i in range(len(src) - n + 1):
            if tuple(src[i:i + n]) in cand_grams:
                return True
    return False


# --------------------------------------------------------------------------
# Theme lexicon (stub mode)
# --------------------------------------------------------------------------

THEME_LEXICON: list[tuple[str, str, str, str, list[str]]] = [
    ("reliable_delivery", "Delivers what was agreed, on time", "strength",
     "Raters consistently described commitments being met without chasing.",
     ["on time", "deadline", "deliver", "reliable", "never misses", "as promised", "ships"]),
    ("quality_of_work", "Output quality needs little rework", "strength",
     "Several raters noted work arriving at a standard that needed minimal correction.",
     ["quality", "no rework", "accurate", "thorough", "polished", "minimal correction"]),
    ("needs_prompting", "Progress depends on reminders", "opportunity",
     "A recurring observation is that work advances after a nudge rather than on its own.",
     ["remind", "chase", "nudge", "follow up", "had to ask", "prompting", "push them"]),
    ("late_risk_signalling", "Risks surface late", "opportunity",
     "Raters described problems becoming visible close to the deadline rather than early.",
     ["last minute", "late", "no warning", "surprise", "too late", "found out"]),
    ("takes_ownership", "Takes matters end to end", "strength",
     "Raters described full ownership of outcomes rather than of tasks.",
     ["ownership", "owns", "end to end", "sees it through", "accountab", "took charge"]),
    ("escalates_decisions", "Escalates decisions that could be made independently", "opportunity",
     "A pattern of checking in on operational calls that raters felt were within reach.",
     ["escalat", "asks before", "checks with", "permission", "defers", "waits for approval"]),
    ("knowledge_transfer", "Documents and hands over well", "strength",
     "Raters pointed to written process and handover making the work less dependent on one person.",
     ["document", "handover", "runbook", "wrote up", "teach", "train", "wiki", "notion"]),
    ("single_point_of_failure", "Work stays dependent on them", "opportunity",
     "Raters described processes that still require this person's direct involvement.",
     ["only person", "depends on them", "bottleneck", "bus factor", "nobody else", "manual"]),
    ("automates_and_simplifies", "Simplifies and automates", "strength",
     "Raters credited concrete reductions in manual work or process complexity.",
     ["automat", "simplif", "script", "streamlin", "reduced", "tooling", "template"]),
    ("business_context", "Connects the work to business priorities", "strength",
     "Raters saw decisions framed against wider goals rather than local task completion.",
     ["business", "strategy", "revenue", "cost", "priorit", "roadmap", "bigger picture"]),
    ("narrow_scope", "Focus stays inside their own remit", "opportunity",
     "Raters described attention concentrated on assigned tasks over surrounding impact.",
     ["own tasks", "narrow", "silo", "own area", "own scope", "not beyond"]),
    ("customer_focus", "Works from the customer's actual problem", "strength",
     "Raters described the underlying need being explored before a solution was proposed.",
     ["customer", "client", "user", "candidate", "end user", "real problem", "underlying"]),
    ("supports_peers", "Makes time to help colleagues", "strength",
     "Raters described help being offered readily, including outside their own objectives.",
     ["helps", "support", "mentor", "pair", "available", "generous", "makes time"]),
    ("hard_under_pressure", "Communication sharpens under pressure", "opportunity",
     "Raters observed a shift in tone when under time or delivery pressure.",
     ["blunt", "harsh", "curt", "abrupt", "defensive", "dismissive", "snaps", "short with"]),
    ("positive_energy", "Steadies the mood of the group", "strength",
     "Raters described a constructive tone maintained through difficult stretches.",
     ["positive", "energy", "calm", "morale", "upbeat", "constructive attitude"]),
]


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------

# Bank wording carries a `{subject}` placeholder that the interview fills with
# the subject's first name after the model has worded the question. Told to keep
# it, the model leaves the name to code; if it drops the token anyway the
# question still reads, just without the name.
KEEP_SUBJECT = (
    "The text {subject} stands for the person being reviewed: keep it exactly as "
    "written, and refer to them as they/their anywhere else, never he or she."
)


def _with_competency(template: str, competency_name: str) -> str:
    """Substitute `{competency}` and nothing else.

    Not `str.format`: the template also carries `{subject}`, which is filled
    later, and format would raise on it.
    """
    return template.replace("{competency}", competency_name)


class StubLLM:
    """Deterministic. Same input, same output, no network."""

    mode = "stub"

    def probe(self, template: str, competency_name: str, **_: object) -> str:
        t0 = time.perf_counter()
        text = _with_competency(template, competency_name)
        trace.record("probe", trace.STUB, trace.TEMPLATE,
                     (time.perf_counter() - t0) * 1000, subject=competency_name)
        return text

    def followup(self, template: str, **_: object) -> str:
        t0 = time.perf_counter()
        trace.record("followup", trace.STUB, trace.TEMPLATE,
                     (time.perf_counter() - t0) * 1000)
        return template

    def judge_specificity(self, question: str, answer: str) -> SpecificityVerdict:
        t0 = time.perf_counter()
        verdict = heuristic_specificity(answer)
        trace.record("judge", trace.STUB, trace.HEURISTIC,
                     (time.perf_counter() - t0) * 1000,
                     detail="specific" if verdict.specific else (verdict.gap or ""))
        return verdict

    def cluster_themes(self, records: list[tuple[str, str]]) -> list[Theme]:
        """records: (ballot_id, redacted_text) -> themes with paraphrase only."""
        hits: dict[str, list[str]] = {}
        for ballot_id, text in records:
            low = (text or "").lower()
            for theme_id, _label, _valence, _para, keys in THEME_LEXICON:
                if any(k in low for k in keys):
                    hits.setdefault(theme_id, []).append(ballot_id)

        themes: list[Theme] = []
        for theme_id, label, valence, paraphrase, _keys in THEME_LEXICON:
            ballots = hits.get(theme_id)
            if ballots:
                themes.append(Theme(theme_id, label, paraphrase, valence, tuple(ballots)))
        themes.sort(key=lambda t: t.n, reverse=True)
        trace.record("themes", trace.STUB, trace.LEXICON, 0,
                     detail=f"{len(themes)} themes from the fixed vocabulary")
        return themes


class OpenAILLM:
    """Live mode. Wording and judgement only — never routing, never scoring."""

    mode = "openai"

    def __init__(self, settings: config.LLMSettings) -> None:
        from langchain_openai import ChatOpenAI

        self._llm = ChatOpenAI(model=settings.model, api_key=settings.api_key, temperature=0.3)
        self._fallback = StubLLM()
        self._model = settings.model

    def probe(self, template: str, competency_name: str, likert_context: str = "", **_: object) -> str:
        base = _with_competency(template, competency_name)
        prompt = (
            "You are conducting an anonymous 360-degree feedback interview.\n"
            "Rephrase the question below so it flows naturally after what the rater has just "
            f"answered. Keep the same intent and scope. One question only, under {PROBE_MAX_WORDS} words. "
            "Do not add commentary, do not give advice, do not mention scores. "
            f"{KEEP_SUBJECT}\n\n"
            f"Context (rater's ratings): {likert_context}\n"
            f"Question: {base}"
        )
        t0 = time.perf_counter()
        try:
            text = self._llm.invoke(prompt).content.strip()
            trace.record("probe", trace.OPENAI, trace.MODEL,
                         (time.perf_counter() - t0) * 1000, model=self._model,
                         subject=competency_name)
            return text
        except Exception as exc:
            # The fallback is deliberate — a demo should not die on a rate limit.
            # Recording it is what stops the result being mistaken for a live one.
            trace.record("probe", trace.OPENAI, trace.FALLBACK,
                         (time.perf_counter() - t0) * 1000, model=self._model,
                         detail=type(exc).__name__, subject=competency_name)
            return base

    def followup(self, template: str, question: str = "", answer: str = "", **_: object) -> str:
        prompt = (
            "You are conducting an anonymous 360-degree feedback interview. The rater's answer "
            "was not specific enough. Ask ONE short follow-up in the spirit of the guidance "
            f"below. Do not evaluate the answer, do not give advice, under {FOLLOWUP_MAX_WORDS} words. "
            f"{KEEP_SUBJECT}\n\n"
            f"Original question: {question}\nTheir answer: {answer}\nGuidance: {template}"
        )
        t0 = time.perf_counter()
        try:
            text = self._llm.invoke(prompt).content.strip()
            trace.record("followup", trace.OPENAI, trace.MODEL,
                         (time.perf_counter() - t0) * 1000, model=self._model)
            return text
        except Exception as exc:
            trace.record("followup", trace.OPENAI, trace.FALLBACK,
                         (time.perf_counter() - t0) * 1000, model=self._model,
                         detail=type(exc).__name__)
            return template

    def judge_specificity(self, question: str, answer: str) -> SpecificityVerdict:
        # The deterministic filter runs first and is authoritative when it fires:
        # a two-word answer needs no model call to be judged thin.
        t0 = time.perf_counter()
        pre = heuristic_specificity(answer)
        if not pre.specific and pre.gap == GAP_TOO_SHORT:
            trace.record("judge", trace.OPENAI, trace.HEURISTIC,
                         (time.perf_counter() - t0) * 1000, model=self._model,
                         detail="too short to be worth a call")
            return pre
        try:
            from pydantic import BaseModel, Field

            class _Verdict(BaseModel):
                specific: bool = Field(description="Is the answer grounded in an observable, concrete situation?")
                gap: str = Field(description="One of: none, needs_example, needs_behaviour, needs_effect")
                # The length of the model's explanation — see JUDGE_REASON_MAX_WORDS.
                reason: str = Field(description=f"Under {JUDGE_REASON_MAX_WORDS} words")

            structured = self._llm.with_structured_output(_Verdict)
            out = structured.invoke(
                "Judge whether this 360-feedback answer is specific enough to be useful to the "
                "person receiving it. Specific means: a real occasion, an observable behaviour, "
                "and some outcome.\n\n"
                f"Question: {question}\nAnswer: {answer}"
            )
            gap = None if (out.specific or out.gap == "none") else out.gap
            trace.record("judge", trace.OPENAI, trace.MODEL,
                         (time.perf_counter() - t0) * 1000, model=self._model,
                         detail="specific" if out.specific else (gap or ""))
            return SpecificityVerdict(out.specific, gap, out.reason)
        except Exception as exc:
            trace.record("judge", trace.OPENAI, trace.FALLBACK,
                         (time.perf_counter() - t0) * 1000, model=self._model,
                         detail=type(exc).__name__)
            return pre

    def cluster_themes(self, records: list[tuple[str, str]]) -> list[Theme]:
        # Deliberately reuses the lexicon rather than free-form clustering: a
        # fixed theme vocabulary keeps reports comparable across rounds, which is
        # what makes trend analysis possible later. The stub's own record already
        # names the lexicon, so live mode does not claim a model call it never made.
        return self._fallback.cluster_themes(records)


def get_llm() -> StubLLM | OpenAILLM:
    settings = config.llm_settings()
    if settings.is_stub:
        return StubLLM()
    try:
        return OpenAILLM(settings)
    except Exception:
        return StubLLM()
