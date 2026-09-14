"""The probe policy: which settings HR may tune, what each one does, and where edits go.

`inputs/questions.json` ships the defaults, and it stays the source of record —
it also holds the probe wording, which is HR- and Legal-reviewed, so an admin
screen must never rewrite it. Edits made from the dashboard are stored as
overrides in `data/probe_policy.json` and layered over those defaults when the
bank loads. That keeps three things true at once:

* the reviewed file is never touched by a web request;
* an edit persists exactly as long as everything else in `data/` does — kept on a
  Render persistent disk, wiped with the rest on a free instance;
* "reset to defaults" is deleting one file.

Only the four settings the engine actually reads are editable. questions.json
also carries a `trigger_priority` list, but no code consults it — the order in
which question types win is fixed in `nodes._trigger_for` — so offering it as an
editable control would let HR change something that changes nothing. It is
described read-only on the dashboard instead, from the real rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config

OVERRIDES_PATH = config.DATA / "probe_policy.json"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    help: str
    minimum: int
    maximum: int


# Plain language on purpose: the audience is HR tuning an interview, not an
# engineer reading a config file. Every sentence here is a claim about the
# engine, so each one is checked against the code that implements it.
FIELDS: tuple[Field, ...] = (
    Field(
        key="max_probed_competencies",
        label="Competencies that get a written question",
        help=(
            "After rating a competency, the reviewer may be asked to write about it. "
            "This is how many of the ten can earn that question in one interview. "
            "Questions go where the ratings carry signal — a very low or very high "
            "score, or items that disagree — so raising this adds depth but makes "
            "every interview longer. \"Not observed\" questions come out of this same "
            "budget. Set it to 0 to stop competency questions entirely — {closing} "
            "still asked."
        ),
        minimum=0,
        maximum=10,
    ),
    Field(
        key="max_followups_per_probe",
        label="Follow-ups per written question",
        help=(
            "If a written answer is too thin — too short, no specific occasion, no "
            "described behaviour, or no stated outcome — the assistant can ask a "
            "follow-up. This caps how many times it presses on a single question, "
            "{closing_incl}. Never applies to \"Not observed\" "
            "questions, which are always optional."
        ),
        minimum=0,
        maximum=5,
    ),
    Field(
        key="max_followups_per_session",
        label="Follow-ups per interview",
        help=(
            "A ceiling across the whole interview, so a reviewer who answers briefly "
            "everywhere is not pressed at every turn. When this is lower than the "
            "per-question limit, this is the one that applies."
        ),
        minimum=0,
        maximum=20,
    ),
    Field(
        key="min_words",
        label="Shortest answer accepted without a follow-up",
        help=(
            "A written answer under this many words — to any question, closing ones "
            "included — is treated as too short and gets a "
            "\"could you say a little more?\" follow-up, if the limits above allow. "
            "It is checked before any model is called, so it costs nothing. Raising "
            "it buys more detail at the price of more follow-ups."
        ),
        minimum=1,
        maximum=60,
    ),
)

FIELD_KEYS = tuple(f.key for f in FIELDS)


def closing_phrases(n: int) -> dict[str, str]:
    """Wording for the fixed closing questions, from how many the bank actually has.

    Counted rather than written into the help text: those questions live in the
    same editable questions.json, and "the four closing questions" would quietly
    become false the day HR or Legal added a fifth.
    """
    if n == 0:
        return {"closing": "no closing questions are configured, so nothing written is",
                "closing_incl": "though no closing questions are configured"}
    if n == 1:
        return {"closing": "the closing question is",
                "closing_incl": "including the closing question"}
    return {"closing": f"the {n} closing questions are",
            "closing_incl": f"including the {n} closing questions"}


def rendered_help(field: Field, n_closing: int) -> str:
    return field.help.format(**closing_phrases(n_closing))


def bundled_defaults(path: Path | None = None) -> dict[str, int]:
    """The shipped values, read straight from questions.json."""
    raw = json.loads((path or config.QUESTIONS_PATH).read_text(encoding="utf-8"))
    policy = raw.get("probe_policy", {})
    return {k: int(policy[k]) for k in FIELD_KEYS if k in policy}


def read_overrides(path: Path | None = None) -> dict[str, int]:
    """Stored edits, ignoring anything unreadable or no longer editable.

    A corrupt or hand-edited file must not take the interview down: the engine
    falls back to the bundled defaults for any key it cannot trust.
    """
    path = path or OVERRIDES_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, int] = {}
    for f in FIELDS:
        value = raw.get(f.key)
        if isinstance(value, int) and not isinstance(value, bool) \
                and f.minimum <= value <= f.maximum:
            clean[f.key] = value
    return clean


def apply_overrides(policy: dict, path: Path | None = None) -> dict:
    """The bundled policy with any stored edits laid over it."""
    return {**policy, **read_overrides(path)}


def validate(form: dict[str, object]) -> dict[str, int]:
    """Coerce and bound-check submitted values, or raise with a reason fit for HR."""
    out: dict[str, int] = {}
    for f in FIELDS:
        raw = form.get(f.key)
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"\"{f.label}\" must be a whole number.") from None
        if not f.minimum <= value <= f.maximum:
            raise ValueError(
                f"\"{f.label}\" must be between {f.minimum} and {f.maximum}."
            )
        out[f.key] = value
    return out


def save_overrides(values: dict[str, int], path: Path | None = None) -> None:
    """Persist validated values and make the next interview turn use them."""
    path = path or OVERRIDES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    _invalidate()


def reset_overrides(path: Path | None = None) -> None:
    (path or OVERRIDES_PATH).unlink(missing_ok=True)
    _invalidate()


def _invalidate() -> None:
    # `load_bank` is lru_cached, and every reader of the policy goes through it —
    # `nodes.plan_probes`, the follow-up budget in `collect`, `llm.min_words`.
    # Without this the dashboard would show the new value while interviews kept
    # running on the old one until the next restart.
    from . import banks
    banks.load_bank.cache_clear()
