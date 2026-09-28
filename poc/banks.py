"""Competency and question banks — the Administrator-managed instrument.

Loaded from inputs/*.json so the bank can be edited without touching code,
which is what the brief's "Administrator must be able to manage the question
and competency bank" requirement means at POC scale.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config, policy


# Who is reading the question. The instrument is worded for a rater by default —
# "does {subject}…" — and that wording is wrong on the one surface where the
# reader is the subject themselves. `self_prompt` in the bank carries the second
# person singular for that case, so the switch is editorial rather than a regex
# applied to someone's carefully worded instrument at render time.
RATER = "rater"
SELF = "self"

# Rater wording names the person once, by first name, where it used to say
# "they": HR testing found non-native readers took "they" as plural. The name is
# a placeholder in the bank rather than a pronoun swap in code, because "do they"
# → "does Ana" is grammar, and the bank's authors are the ones who should write it.
SUBJECT_TOKEN = "{subject}"
# Used when there is no name to put in — a bank rendered outside an interview,
# or a subject with a blank display name. Reads correctly in every template.
NO_NAME = "this person"
_SUBJECT = re.compile(r"(^|[.!?]\s+)?\{subject\}")


def first_name(display_name: str | None) -> str:
    """The name the questions use: the first word of the display name.

    "María José Pérez" comes out as "María". That is a known limit, accepted for
    now rather than adding a second name field every subject would need filled in.
    """
    parts = (display_name or "").split()
    return parts[0] if parts else ""


def fill_subject(text: str, name: str | None) -> str:
    """Put the subject's name into bank wording.

    Plain substitution, not `str.format`: the text may also carry `{competency}`
    or a brace a model wrote, and neither should raise here. The fallback is
    capitalised at the start of a sentence, where "this person seems…" would
    otherwise open a question in lower case.
    """
    name = name or NO_NAME

    def sub(m: re.Match) -> str:
        lead = m.group(1)
        if lead is None:
            return name
        return lead + name[:1].upper() + name[1:]

    return _SUBJECT.sub(sub, text or "")


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    prompt: str
    competency_id: str
    self_prompt: str = ""

    def prompt_for(self, perspective: str = RATER, subject: str = "") -> str:
        """The wording this reader should see, with `subject`'s name filled in.

        Falls back to the rater phrasing rather than raising: a bank edited to add
        an item and not its `self_prompt` should render a slightly-off question,
        not take down the self-assessment page. On that fallback the subject is
        reading about themselves, so `subject` is ignored and the question says
        "this person" rather than addressing them by name in the third person.
        """
        if perspective == SELF:
            return self.self_prompt or fill_subject(self.prompt, "")
        return fill_subject(self.prompt, subject)


@dataclass(frozen=True)
class Competency:
    id: str
    name: str
    definition: str
    dimension: str
    anchor_set: str
    items: tuple[Item, ...]
    draft: bool = False


@dataclass(frozen=True)
class Bank:
    competencies: tuple[Competency, ...]
    anchors: dict
    anchors_self: dict
    scale: dict
    dimensions: tuple[dict, ...]
    open_probes: dict
    followup_templates: dict
    qualitative_fixed: tuple[dict, ...]
    probe_policy: dict

    def competency(self, cid: str) -> Competency:
        for c in self.competencies:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def item(self, item_id: str) -> Item:
        for c in self.competencies:
            for i in c.items:
                if i.id == item_id:
                    return i
        raise KeyError(item_id)

    @property
    def all_items(self) -> tuple[Item, ...]:
        return tuple(i for c in self.competencies for i in c.items)

    def anchors_for(self, competency_id: str, perspective: str = RATER) -> dict:
        """Level descriptions for a competency, in the reader's own grammar.

        The anchors sit directly beneath the question on the self-assessment page,
        so leaving them in the third person while the question asks "do you…"
        would put both voices in one paragraph.
        """
        anchor_set = self.competency(competency_id).anchor_set
        if perspective == SELF:
            return self.anchors_self.get(anchor_set, self.anchors[anchor_set])
        return self.anchors[anchor_set]


@lru_cache(maxsize=1)
def load_bank() -> Bank:
    comp_raw = json.loads(config.COMPETENCIES_PATH.read_text(encoding="utf-8"))
    q_raw = json.loads(config.QUESTIONS_PATH.read_text(encoding="utf-8"))

    competencies = tuple(
        Competency(
            id=c["id"],
            name=c["name"],
            definition=c["definition"],
            dimension=c["dimension"],
            anchor_set=c["anchor_set"],
            draft=c.get("draft", False),
            items=tuple(
                Item(id=i["id"], label=i["label"], prompt=i["prompt"],
                     competency_id=c["id"], self_prompt=i.get("self_prompt", ""))
                for i in c["items"]
            ),
        )
        for c in comp_raw["competencies"]
    )

    return Bank(
        competencies=competencies,
        anchors=comp_raw["anchor_sets"],
        anchors_self=comp_raw.get("anchor_sets_self", {}),
        scale=comp_raw["scale"],
        dimensions=tuple(comp_raw["dimensions"]),
        open_probes=q_raw["open_probes"],
        followup_templates=q_raw["followup_templates"],
        qualitative_fixed=tuple(q_raw["qualitative_fixed"]),
        # Stored dashboard edits layered over the shipped defaults. Applied here,
        # inside the cached loader, so every reader of the policy agrees and one
        # `cache_clear()` from `policy.save_overrides` updates them all at once.
        probe_policy=policy.apply_overrides(q_raw["probe_policy"]),
    )
