"""Measured recall for the guardrails, against a corpus of real phrasings.

`test_guardrails.py` asks whether specific rules behave. This asks a different
and harder question: of the ways people actually write these things, how many do
we catch? The first version of the matcher scored well on the former and had a
systematic hole in the latter — every crisis verb was present-tense only, so
"killing myself" walked straight through a pattern that knew "kill myself".

The corpus in `fixtures/guardrail_phrases.yaml` is the specification. Phrasings
are added there first and patterns are fixed second, which is the order that
stops the tests from being written to fit whatever the regex already does.

False positives are held to a stricter standard than false negatives, and not
because they matter more in the abstract. A missed crisis phrase is caught by the
next tier of a real support system; a false refusal silently discards the answer
someone just wrote, and a false crisis halts a working interview and signposts
EAP at somebody talking about a secondment. Both teach raters that the system
overreacts, which is how you end up with feedback nobody says anything real in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from poc.graph import guardrails

FIXTURE = Path(__file__).parent / "fixtures" / "guardrail_phrases.yaml"

# Recall over the whole corpus, known misses included. Below this the guardrail
# is not doing its job; above it, the remaining gap is documented rather than
# unknown. Raise it when the matcher improves — never lower it to make a suite pass.
MIN_OVERALL_RECALL = 0.85


def _load() -> dict[str, list[dict]]:
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for label, entries in raw.items():
        out[label] = [
            {"phrase": e} if isinstance(e, str) else dict(e)
            for e in entries
        ]
    return out


CORPUS = _load()


def _cases(label: str, *, known_miss: bool):
    return [
        pytest.param(e, id=e["phrase"][:60])
        for e in CORPUS[label]
        if ("known_miss" in e) is known_miss
    ]


# ---------------------------------------------------------------------------
# what must be caught
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry", _cases("crisis", known_miss=False))
def test_crisis_phrasings_are_caught(entry):
    assert guardrails.check(entry["phrase"]).kind == guardrails.KIND_CRISIS


@pytest.mark.parametrize("entry", _cases("distress", known_miss=False))
def test_distress_phrasings_are_caught(entry):
    """Distress must not escalate to crisis — that would halt the interview.

    The README's reasoning about "overwhelmed" applies to the whole tier: firing
    a halt on ordinary hard-quarter language trains raters to self-censor.
    """
    assert guardrails.check(entry["phrase"]).kind == guardrails.KIND_DISTRESS


@pytest.mark.parametrize("entry", _cases("refuse", known_miss=False))
def test_refusals_are_caught_with_the_right_code(entry):
    verdict = guardrails.check(entry["phrase"])
    assert verdict.kind == guardrails.KIND_REFUSE
    if entry.get("code"):
        assert verdict.code == entry["code"]


# ---------------------------------------------------------------------------
# what must NOT fire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry", _cases("ok", known_miss=False))
def test_ordinary_workplace_language_does_not_fire(entry):
    verdict = guardrails.check(entry["phrase"])
    assert verdict.kind == guardrails.KIND_OK, (
        f"false positive ({verdict.code}): this sentence is ordinary feedback, and "
        f"firing on it either halts the interview or discards the rater's answer"
    )


# ---------------------------------------------------------------------------
# the known misses stay honest
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label,entry",
    [
        pytest.param(label, e, id=f"{label}:{e['phrase'][:45]}")
        for label in ("crisis", "distress", "refuse")
        for e in CORPUS[label] if "known_miss" in e
    ],
)
def test_known_misses_are_still_missed(label, entry):
    """Deliberately asserts the gap, so closing one fails the suite.

    A matcher improvement that starts catching one of these is good news, and
    this failing is how it gets noticed and the entry promoted out of the
    known-miss list. Without it the list quietly becomes a lie.
    """
    expected = {"crisis": guardrails.KIND_CRISIS,
                "distress": guardrails.KIND_DISTRESS,
                "refuse": guardrails.KIND_REFUSE}[label]
    assert guardrails.check(entry["phrase"]).kind != expected, (
        f"good news: {entry['phrase']!r} is now caught. "
        f"Promote it out of known_miss in {FIXTURE.name} and raise MIN_OVERALL_RECALL."
    )


# ---------------------------------------------------------------------------
# the number
# ---------------------------------------------------------------------------

def test_overall_recall_meets_the_target(capsys):
    hit = total = 0
    for label, kind in (("crisis", guardrails.KIND_CRISIS),
                        ("distress", guardrails.KIND_DISTRESS),
                        ("refuse", guardrails.KIND_REFUSE)):
        for entry in CORPUS[label]:
            total += 1
            hit += guardrails.check(entry["phrase"]).kind == kind

    recall = hit / total
    with capsys.disabled():
        print(f"\n  guardrail recall: {hit}/{total} = {recall:.0%} "
              f"(target {MIN_OVERALL_RECALL:.0%})")
    assert recall >= MIN_OVERALL_RECALL, (
        f"recall {recall:.0%} is below the {MIN_OVERALL_RECALL:.0%} target"
    )


def test_no_false_positives_on_ordinary_language():
    fired = [
        (e["phrase"], guardrails.check(e["phrase"]).code)
        for e in CORPUS["ok"]
        if guardrails.check(e["phrase"]).kind != guardrails.KIND_OK
    ]
    assert not fired, f"{len(fired)} false positives: {fired}"
