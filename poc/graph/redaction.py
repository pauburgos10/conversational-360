"""Deterministic redaction, applied when a report is RENDERED FOR THE SUBJECT.

This used to run before persistence and destroy the original. The client's
demo-2 clarification changed that: HR administers the round and reads answers as
written, so masking exists to protect the *subject's* copy, not everyone's. The
rules did not change; where they run did.

Design notes
------------
* This runs on the way *out*, keyed on audience — see `poc.reporting`. The store
  holds what the rater wrote.
* The cost of that move, stated plainly: a raw answer now does reach disk, so a
  bug in the reporting layer could leak one where previously it could not. Three
  things carry that weight instead — `AUDIENCE_SUBJECT` is the default so a
  forgetful caller fails closed, the subject's copy is asserted in the test
  suite, and raw retention is bounded by the 12-month rule in
  `reporting-rules.yaml`.
* What it buys: the mask is no longer frozen at write time. Add a name to the
  gazetteer today and every past answer is masked in the subject's copy from the
  next render — previously that name stayed exposed forever in rows already
  written.
* `collect` still calls this once on the way in, but only to record which codes
  WILL fire, so the rater's pre-submit preview and the subject's report cannot
  drift apart.
* Rules are deterministic regex, not an LLM. That keeps the tests hermetic and,
  more importantly, makes the control auditable: HR/Legal can read the rule list.
* The bias is deliberately toward over-redaction. For a confidentiality control,
  a false positive costs a slightly duller sentence; a false negative can
  identify a rater to the subject. Those are not symmetric.
* Production would add a proper PII/NER service behind the same interface. The
  `Redactor.apply` signature is the seam for that.

Note on the rule families, since one of them changed meaning: `ROLE`, `SELF_ID`
and `TEAM_SIZE` exist to stop the SUBJECT inferring who reviewed them, and are
still needed for exactly that. `NAME`, `EMAIL`, `PHONE`, `URL`, `HANDLE` and
`TICKET` protect *third parties named in the feedback* — colleagues and customers
who never consented to anything — and are a GDPR data-minimisation control that
this clarification does not touch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Codes are stable identifiers: they end up in the audit trail and the demo.
CODE_EMAIL = "EMAIL"
CODE_PHONE = "PHONE"
CODE_URL = "URL"
CODE_HANDLE = "HANDLE"
CODE_TICKET = "TICKET"
CODE_NAME = "NAME"
CODE_SELF_ID = "SELF_ID"
CODE_ROLE = "ROLE"
CODE_TEAM_SIZE = "TEAM_SIZE"

# Words that look like names to a capitalisation heuristic but are not.
ALLOWLIST = {
    "Pluralit", "Talent", "Acquisition", "Legal", "Immigration", "Business",
    "Process", "Comms", "Admin", "Finance", "Support", "Sourcer", "Partner",
    "Lead", "Analyst", "Trainee", "Delivery", "Autonomy", "Customer",
    "Orientation", "Innovation", "Impact", "Systemic", "Decision", "Making",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Q1", "Q2", "Q3", "Q4", "OKR", "OKRs", "KPI", "KPIs", "HR", "AI",
    "English", "Spanish", "Slack", "Notion", "Workday",
}

# Phrases that identify the rater by their relationship to the subject.
# Round 1 forbids any breakdown by role, so free text disclosing it is a leak.
_ROLE_PATTERNS = [
    r"\bas (?:their|his|her|the)\s+(?:direct\s+)?(?:manager|line manager|lead|boss|supervisor)\b",
    r"\bas (?:their|his|her|the)\s+(?:direct\s+)?report\b",
    r"\bas (?:their|his|her|the)\s+peer\b",
    r"\bi\s+(?:am|'m)\s+(?:their|his|her)\s+(?:manager|lead|boss|supervisor|report|peer)\b",
    r"\bi\s+(?:manage|supervise|lead)\s+(?:them|him|her|this person)\b",
    r"\bi\s+report\s+(?:to|into)\s+(?:them|him|her|this person)\b",
    r"\b(?:they|he|she)\s+reports?\s+(?:to|into)\s+me\b",
    r"\bin\s+my\s+(?:role|position)\s+as\s+[a-z &]+\b",
    r"\bas\s+(?:a|the)\s+(?:sourcer|talent partner|talent lead|recruiter)\b",
    r"\bon\s+the\s+(?:legal(?:\s*&\s*| and )immigration|business process|comms|admin(?:\s*&\s*| and )finance)\s+team\b",
]

# Constructions that identify the rater by uniqueness of position.
# `_NOUNS` stops a match at the next pronoun so a phrase pattern consumes the
# self-identifying noun phrase and not the whole clause that follows it.
_NOUNS = r"(?:(?!\b(?:i|we|they|he|she|it|you)\b)[a-z ]){2,30}"

_SELF_ID_PATTERNS = [
    rf"\bas\s+(?:their|his|her|the)\s+only\s+{_NOUNS}",
    rf"\bi\s+(?:am|'m)\s+the\s+only\s+{_NOUNS}",
    rf"\bbeing\s+the\s+(?:only|sole)\s+{_NOUNS}",
    r"\bthe\s+only\s+(?:person|one)\s+who\s+(?:(?!\b(?:i|we|they|he|she|it|you)\b)[a-z ]){2,40}",
    rf"\bsince\s+i\s+(?:am|'m)\s+the\s+(?:only|sole)\s+{_NOUNS}",
]

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Phrase-level first, so a whole construction is replaced rather than
    # partially chewed up by the name rules below.
    *[(CODE_SELF_ID, re.compile(p, re.I)) for p in _SELF_ID_PATTERNS],
    *[(CODE_ROLE, re.compile(p, re.I)) for p in _ROLE_PATTERNS],
    (CODE_TEAM_SIZE, re.compile(r"\bmy\s+team\s+of\s+\d+\b", re.I)),
    (CODE_URL, re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    (CODE_EMAIL, re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    (CODE_PHONE, re.compile(r"(?<!\w)(?:\+\d{1,3}[\s-]?)?(?:\(\d{2,4}\)[\s-]?)?\d{3}[\s-]?\d{3}[\s-]?\d{2,4}(?!\w)")),
    (CODE_HANDLE, re.compile(r"(?<![\w.])@[A-Za-z][\w.-]{2,}")),
    (CODE_TICKET, re.compile(r"\b[A-Z]{2,10}-\d{1,6}\b|(?<!\w)#\d{2,6}\b")),
    (CODE_NAME, re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Sr|Sra)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")),
]

# A reviewer naming their own job title: "as a business process analyst, I…".
# The fixed `_ROLE_PATTERNS` only know the talent titles the POC started with, so
# the organisation's real titles come from the roster (`roster.redaction_roles`)
# and are compiled into one rule per Redactor. Seniority words are stripped as
# well, because a "Senior Talent Partner" writes "as a talent partner".
_ROLE_LEAD = r"\b(?:as|i\s+am|i'm|being)\s+(?:a|an|the|their|his|her|our|your)\s+"
_SENIORITY = re.compile(r"^(?:senior|junior|lead|principal|associate|chief)\s+", re.I)
_MIN_ROLE_LEN = 4


def _role_rule(roles: set[str] | None) -> re.Pattern[str] | None:
    titles: set[str] = set()
    for role in roles or ():
        title = " ".join(role.lower().split())
        for variant in (title, _SENIORITY.sub("", title)):
            if len(variant) >= _MIN_ROLE_LEN:
                titles.add(variant)
    if not titles:
        return None
    # Longest first, so "talent partner lead" wins over "talent partner".
    alternatives = "|".join(
        re.escape(t).replace(r"\ ", r"\s+") for t in sorted(titles, key=len, reverse=True)
    )
    return re.compile(rf"{_ROLE_LEAD}(?:{alternatives})\b", re.I)


# Two adjacent capitalised words, evaluated against the allowlist before firing.
_FULL_NAME = re.compile(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,15})\b")


@dataclass
class Finding:
    code: str
    length: int  # length of what was removed; never the content itself


@dataclass
class RedactionResult:
    text: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [f.code for f in self.findings]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.code] = out.get(f.code, 0) + 1
        return out

    @property
    def clean(self) -> bool:
        return not self.findings


# A derived token must look like a name fragment before it earns a rule of its
# own. This drops particles ("de", "la", "van") that would otherwise match
# ordinary Spanish and Dutch text everywhere.
_MIN_DERIVED_LEN = 3


def _derive(line: str) -> set[str]:
    """Name fragments worth matching on their own, from a full-name line."""
    parts = line.split()
    if len(parts) < 2:
        return set()
    return {
        part for part in parts
        if len(part) >= _MIN_DERIVED_LEN and part[:1].isupper() and part not in ALLOWLIST
    }


class Redactor:
    """Applies the rule set.

    Names are matched in two tiers, because the two carry different confidence:

    * `gazetteer` — entries an administrator actually wrote. Matched
      **case-insensitively**: "ana torres" typed casually in a chat box is
      unambiguous, and raters do not capitalise carefully.
    * `derived` — single tokens this class inferred by splitting a full name.
      Matched **case-sensitively**. Half the surnames in any Spanish-speaking
      roster are also ordinary words — *torres*, *marcos*, *salas*, *blanco*,
      *tomas* — and matching those case-insensitively turns one sentence of
      Spanish feedback into five false positives. Requiring the capital is what
      separates a reference to a person from a reference to a thing.

    The deliberate consequence: a bare lowercase surname is not redacted. That
    case is genuinely ambiguous, and the full-name form that a rater is far more
    likely to write is caught by the tier above.
    """

    def __init__(self, gazetteer: set[str] | None = None,
                 derived: set[str] | None = None,
                 roles: set[str] | None = None) -> None:
        self.gazetteer = {g.strip() for g in (gazetteer or set()) if g.strip()}
        self.derived = {d.strip() for d in (derived or set()) if d.strip()} - self.gazetteer
        self.role_rule = _role_rule(roles)

    @classmethod
    def from_file(cls, path: Path | str | None) -> "Redactor":
        explicit: set[str] = set()
        derived: set[str] = set()
        if path and Path(path).exists():
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    explicit.add(line)
                    derived |= _derive(line)
        return cls(explicit, derived)

    def apply(self, text: str) -> RedactionResult:
        if not text:
            return RedactionResult(text="", findings=[])

        findings: list[Finding] = []
        out = text

        for code, pattern in _PATTERNS:
            def _sub(m: re.Match[str], _code: str = code) -> str:
                findings.append(Finding(_code, len(m.group(0))))
                return f"[{_code}]"
            out = pattern.sub(_sub, out)

        if self.role_rule is not None:
            def _sub_role(m: re.Match[str]) -> str:
                findings.append(Finding(CODE_ROLE, len(m.group(0))))
                return f"[{CODE_ROLE}]"
            out = self.role_rule.sub(_sub_role, out)

        def _sub_name(m: re.Match[str]) -> str:
            findings.append(Finding(CODE_NAME, len(m.group(0))))
            return f"[{CODE_NAME}]"

        # Longest first, so "Ana Torres" collapses to one [NAME] rather than two.
        # Explicit entries run before derived ones for the same reason: a full
        # name is always longer than the fragments taken out of it.
        for name in sorted(self.gazetteer, key=len, reverse=True):
            out = re.compile(rf"\b{re.escape(name)}\b", re.I).sub(_sub_name, out)
        for name in sorted(self.derived, key=len, reverse=True):
            out = re.compile(rf"\b{re.escape(name)}\b").sub(_sub_name, out)

        # Heuristic full names, skipped when either half is an ordinary word.
        def _sub_full(m: re.Match[str]) -> str:
            first, second = m.group(1), m.group(2)
            if first in ALLOWLIST or second in ALLOWLIST:
                return m.group(0)
            # Sentence-initial capitals are usually just sentences.
            start = m.start()
            preceding = out[:start].rstrip()
            if not preceding or preceding.endswith((".", "!", "?")):
                return m.group(0)
            findings.append(Finding(CODE_NAME, len(m.group(0))))
            return f"[{CODE_NAME}]"

        out = _FULL_NAME.sub(_sub_full, out)

        # Collapse runs like "[NAME] [NAME]" produced by adjacent hits.
        out = re.sub(r"(\[([A-Z_]+)\])(\s+\1)+", r"\1", out)
        return RedactionResult(text=out, findings=findings)


def redact(text: str, gazetteer: set[str] | None = None,
           derived: set[str] | None = None) -> RedactionResult:
    return Redactor(gazetteer, derived).apply(text)
