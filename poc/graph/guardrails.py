"""Guardrails: crisis signposting and scope refusals.

The brief sets two hard business rules that this module enforces:

* "No clinical or mental-health advice; signpost to professional support if the
  user expresses crisis."
* "Do not use feedback for automated performance scores" — the assistant must
  not answer HR-decision questions, because answering them is exactly the
  automated-decision behaviour the brief forbids.

Detection is deterministic phrase matching, not an LLM call. Three reasons:
a guardrail that depends on a network round-trip fails open when the network
fails; the rule list is reviewable by HR/Legal; and it makes the demo
reproducible. `llm_second_opinion` is the seam for adding a model-based check
on top, and it is off by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KIND_OK = "ok"
KIND_CRISIS = "crisis"
KIND_DISTRESS = "distress"
KIND_REFUSE = "refuse"

EAP_PLACEHOLDER = "[Link to EAP or Internal HR Contact for Well-being]"

# High-signal only. "Overwhelmed" alone is deliberately NOT here: it is common
# in ordinary workplace feedback and firing on it would train raters to
# self-censor.
# Every verb carries its inflections. The first version of this list was
# present-tense only, so "killing myself", "hurting myself" and "self-harming"
# all walked past it — a systematic gap, not three typos, and the worst possible
# one to have: a rater in distress got no signposting and the interview simply
# carried on asking about competencies.
_CRISIS = [
    r"\bkill(?:ing)? (?:myself|me)\b",
    # "end my life" and "end it all" only. The bare "end my" this used to allow
    # fired on "end my involvement", "end my secondment" — common work phrasing,
    # and halting an interview over it is both wrong and memorable.
    r"\bend(?:ing)? (?:my life|it all)\b",
    r"\bsuicid(?:e|al)\b",
    r"\bself[- ]?harm(?:ing|ed|s)?\b",
    r"\bhurt(?:ing)? myself\b",
    r"\bdon'?t want to (?:be here|live|go on)\b",
    r"\bcan'?t go on\b", r"\bno reason to (?:live|continue)\b",
    r"\bi want to disappear\b",
]

# Lower tier: acknowledge and offer support, but do not halt the session.
_DISTRESS = [
    r"\bburn(?:ing|ed|t) out\b", r"\bburnout\b",
    # Not anchored on "i": "we can't cope" is the same signal.
    r"\bcan'?t cope\b", r"\bbreaking point\b",
    r"\b(?:completely|totally) overwhelmed\b",
    r"\bcrying (?:at work|every day)\b",
    r"\bdread(?: coming)? (?:to )?work\b",
    r"\bmy mental health\b",
]

_REFUSALS: list[tuple[str, str, str]] = [
    (
        "HR_DECISION",
        r"\bshould (?:i|we|they) (?:fire|dismiss|promote|demote|let go|terminate)\b"
        r"|\bshould (?:they|he|she) be (?:promoted|fired|dismissed|let go)\b"
        r"|\bwhat (?:salary|raise|band|level|seniority) (?:should|do)\b"
        r"|\bdo they deserve (?:a|the) (?:raise|promotion|bonus)\b"
        r"|\bwhat(?:'s| is) (?:their|his|her) score\b"
        r"|\brate them overall\b|\bgive (?:them|him|her) a (?:score|grade|rating) overall\b",
        "I can't weigh in on promotion, pay, or performance decisions — this system is "
        "deliberately built not to produce them. Your feedback goes into an aggregated, "
        "anonymous report, and a person makes those calls with much more context than I have.",
    ),
    (
        "CLINICAL_ADVICE",
        r"\b(?:am i|are they|is (?:he|she)) depressed\b"
        r"|\bdo (?:i|they) have (?:anxiety|adhd|depression|burnout syndrome)\b"
        # Scoped to a person. A bare \bdiagnos(?:e|is|ed)\b refused
        # "she diagnosed the root cause in an afternoon".
        r"|\bdiagnos(?:e|ing) (?:me|us|them|him|her|what)\b"
        r"|\b(?:my|their|his|her) diagnosis\b"
        r"|\bwhat should i do about my (?:anxiety|depression|stress|mental health)\b"
        r"|\bcan you (?:counsel|therap)\w*\b",
        "I'm not able to give clinical or mental-health advice — I'm a feedback assistant, "
        f"not a clinician. If you'd like support, please contact {EAP_PLACEHOLDER}.",
    ),
    (
        "PRIVATE_LIFE",
        # "health" dropped: it refused "their health and safety write-up".
        # "illness" carries the private-life signal without the collision.
        r"\b(?:their|his|her) (?:marriage|divorce|partner|husband|wife|kids|children|illness|religion|pregnan\w*)\b"
        # Stated directly rather than possessively. "she is pregnant so this will
        # slip" is the discriminatory form this rule exists for, and requiring
        # "her pregnancy" missed it entirely.
        r"|\b(?:is|was|being) pregnan\w*\b"
        r"|\bpersonal (?:life|problems) at home\b",
        "Let's keep this to what you have observed at work. Anything about someone's personal "
        "life sits outside the scope of this review and wouldn't be appropriate to record.",
    ),
    (
        "COMPARE_NAMED",
        # (?-i:...) restores case sensitivity inside an re.I pattern. Without it
        # [A-Z][a-z]+ matched ANY word, so "better than average", "worse than last
        # quarter" and "compared to previous reviews" were all refused and the
        # rater's turn discarded. Comparative language is ordinary in 360 feedback.
        #
        # Requiring the capital is the same two-tier reasoning the redactor uses:
        # it separates a reference to a person from a reference to a thing. The
        # deliberate consequence is that an uncapitalised name is not caught.
        r"\b(?:better|worse|stronger|weaker) than (?-i:[A-Z][a-z]+)\b"
        r"|\bcompared to (?-i:[A-Z][a-z]+)\b",
        "I'd rather not record comparisons against another named colleague — that person hasn't "
        "consented to being assessed here. Could you describe what you observed on its own terms?",
    ),
]

_CRISIS_RE = [re.compile(p, re.I) for p in _CRISIS]
_DISTRESS_RE = [re.compile(p, re.I) for p in _DISTRESS]
_REFUSAL_RE = [(code, re.compile(p, re.I), msg) for code, p, msg in _REFUSALS]

CRISIS_MESSAGE = (
    "Thank you for saying that — it matters more than the rest of this survey.\n\n"
    "I'm going to stop the feedback questions here. I'm not able to offer clinical or "
    "mental-health support, but people who can are available to you:\n\n"
    f"    If you feel overwhelmed or need support, please contact {EAP_PLACEHOLDER}.\n\n"
    "Your progress has been saved. You can come back to this whenever you want to — "
    "or not at all. That is genuinely fine."
)

DISTRESS_PREFIX = (
    "That sounds hard, and I'm sorry you're dealing with it. If it would help to talk to "
    f"someone, {EAP_PLACEHOLDER} is there for exactly this.\n\n"
    "I'll carry on with the feedback questions — stop any time you want to.\n\n"
)


@dataclass
class Verdict:
    kind: str
    code: str | None = None
    message: str | None = None

    @property
    def halts(self) -> bool:
        return self.kind == KIND_CRISIS

    @property
    def blocks_recording(self) -> bool:
        """Crisis and refusals mean the turn is not recorded as an answer."""
        return self.kind in (KIND_CRISIS, KIND_REFUSE)


def check(text: str) -> Verdict:
    if not text or not text.strip():
        return Verdict(KIND_OK)

    # Crisis outranks everything, including a refusal match in the same turn.
    for pattern in _CRISIS_RE:
        if pattern.search(text):
            return Verdict(KIND_CRISIS, "CRISIS", CRISIS_MESSAGE)

    for code, pattern, message in _REFUSAL_RE:
        if pattern.search(text):
            return Verdict(KIND_REFUSE, code, message)

    for pattern in _DISTRESS_RE:
        if pattern.search(text):
            return Verdict(KIND_DISTRESS, "DISTRESS", DISTRESS_PREFIX)

    return Verdict(KIND_OK)
