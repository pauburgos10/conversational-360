"""Generate a synthetic round.

The roster is designed, not random. A demo where every cell publishes proves
nothing, and a demo where everything is suppressed is unreadable. So the shape
below deliberately produces at least one cell for each suppression rule:

* S3 and S6 attract too few responses           -> MIN_N
* S4 has one rater who completed everything and
  three who answered only a couple of items, so
  the dimension figures rest on one person       -> DOMINANCE
* one competency hidden inside a published
  dimension leaves its value recoverable         -> COMPLEMENTARY

Partial completion is not a contrived case: it is the most common shape in real
360 rounds, where raters answer only about what they have actually seen. Note
that DOMINANCE cannot fire on a three-item competency at min_n=4 — three other
ballots contributing one answer each cap the leader at exactly 50%. It is
structurally a dimension- and overall-level rule.

**Partial ballots are seeded as "not observed" declines**, which is how a real
rater produces one: `collect` accepts the opt-out and records the item in
`declined_item` instead of scoring it. Until that option existed this script was
generating a shape the product could not, and two of the three suppression rules
were being demonstrated on unreachable data.

Free text is stored **as written**, matching the engine — masking happens when a
report is rendered for the subject (see `poc.reporting`). The redactor still runs
here to record which codes will fire, exactly as `collect` does, and a few
answers are seeded with names, emails and self-identifying phrases so those
counts are real.
"""

from __future__ import annotations

import random

from . import banks, config, store
from .roster import load_subjects
from .graph.redaction import Redactor

ROUND_ID = "2026-H2"

# Who is being reviewed comes from inputs/subjects.csv — the one roster file an
# administrator edits before a real round. Everything below (how many ballots,
# what raters tend to say) is synthetic-data shaping and stays in this script.
SUBJECTS = load_subjects()

# Characteristic themes per subject, as indices into TEXTS. Raters mostly echo
# the subject's actual traits and occasionally say something idiosyncratic, which
# is what lets some themes clear the reporting threshold while others do not.
# Drawing uniformly at random would leave every theme below min_ballots_per_theme
# and produce a report with nothing in it.
TRAITS = {
    "S1": [0, 1, 4, 8, 12],       # reliable, high quality, owns it, automates, helps
    "S2": [2, 3, 5, 9, 10],       # needs reminders, late risk, escalates, business-aware
    "S3": [0, 6, 11],
    "S4": [2, 7, 10, 13],         # reminders, bottleneck, narrow, blunt under pressure
    "S5": [4, 6, 8, 9, 14],       # ownership, documents, automates, strategic, steadying
    "S6": [1, 12],
}

# How each subject rates themselves relative to how raters see them. This is the
# whole point of the comparison: S4 has a blind spot, S5 undersells, S1 is close.
SELF_SKEW = {"S1": +0.1, "S2": -0.2, "S3": +0.3, "S4": +0.8, "S5": -0.5, "S6": 0.0}

# subject -> (n_ballots, centre of the score distribution)
PROFILE = {
    "S1": (8, 2.4),
    "S2": (6, 2.0),
    "S3": (2, 2.2),   # below min_n on every cell
    "S4": (4, 1.8),   # one complete rater + three partial -> dominance
    "S5": (4, 2.6),
    "S6": (1, 2.0),   # below min_n on every cell
}

TEXTS = [
    "They hit every agreed deadline last quarter and flagged the vendor risk in week 2, which meant we still shipped on time.",
    "Quality is consistently high; the last three handovers needed no rework at all, so review time dropped.",
    "I usually have to remind them twice before a task closes out, which pushes the whole chain back a few days.",
    "Problems tend to surface at the last minute rather than early, so we lose the chance to re-plan.",
    "They own their area end to end and chase the outcome, not just the ticket, which saved the migration in March.",
    "They escalate operational calls that they could make themselves; waiting for approval adds a day most weeks.",
    "They documented the whole onboarding process in Notion last month, so two other people can now run it.",
    "The reconciliation still only runs when they are here, which makes them a bottleneck every close.",
    "They automated the weekly report in April and it cut a manual half-day out of the process entirely.",
    "Decisions are framed against business priorities rather than the immediate task, which helped us drop a low-value workstream.",
    "Focus stays inside their own scope; the effect on other areas is often noticed only afterwards.",
    "They dig into what the customer actually needs before proposing anything, which avoided building the wrong integration.",
    "They make time to help peers even when it is outside their objectives, and that lifted the whole team in Q2.",
    "Under delivery pressure the tone gets blunt, which lands badly with people who do not know them well.",
    "They keep the mood constructive through difficult stretches, which mattered during the November crunch.",
    "Ana Torres and I paired on this; you can reach me at ana.torres@pluralit.com if you need detail.",
    "As their only direct report I see this constantly, and FEAT-4412 was a clear example of it going well.",
    "Good.",
    "They are reliable and strong overall, a real asset to have around.",
]


def _score(centre: float, rng: random.Random) -> int:
    raw = rng.gauss(centre, 0.55)
    return max(1, min(3, round(raw)))


def seed(reset: bool = True, seed_value: int = 20260825) -> dict:
    rng = random.Random(seed_value)
    bank = banks.load_bank()
    redactor = Redactor.from_file(config.GAZETTEER_PATH)

    if reset:
        config.RESPONSES_DB.unlink(missing_ok=True)
    store.init()

    for subject in SUBJECTS:
        store.upsert_subject(subject.subject_id, subject.display_name,
                             subject.cohort, subject.role)

    all_items = list(bank.all_items)
    c4_items = {i.id for i in bank.competency("C4").items}
    c6_items = {i.id for i in bank.competency("C6").items}

    stats = {"ballots": 0, "likert": 0, "declined": 0, "open": 0,
             "self_assessments": 0, "redactions": {}}

    for subject in SUBJECTS:
        sid = subject.subject_id
        n_ballots, centre = PROFILE.get(sid, (4, 2.0))
        for b in range(n_ballots):
            ballot_id = f"{sid}-b{b:02d}"
            store.create_ballot(sid, ROUND_ID, ballot_id=ballot_id)

            items = list(all_items)

            # DOMINANCE: on S5, only the first rater answers C6 at all, so that
            # competency clears min_n nowhere and its evidence is one person's.
            if sid == "S5" and b > 0:
                items = [i for i in items if i.id not in c6_items]

            # DOMINANCE: on S4 only the first rater completed the questionnaire;
            # the rest answered two items. The dimension figure clears min_n but
            # rests overwhelmingly on one person, which is what the rule catches.
            if sid == "S4" and b > 0:
                items = [i for i in items if i.id in {"C1.1", "C1.2"}]

            # COMPLEMENTARY: on S2, C4 is answered by only three raters. The
            # behavioural dimension around it stays published, which leaves C4
            # recoverable as the residual unless a sibling is also suppressed.
            if sid == "S2" and b >= 3:
                items = [i for i in items if i.id not in c4_items]

            # Everything the rater was asked but did not rate is an explicit
            # "not observed", not a silent gap — the only shape the live
            # instrument can produce.
            rated = {i.id for i in items}
            declined = [i.id for i in all_items if i.id not in rated]

            likert = {i.id: _score(centre, rng) for i in items}

            traits = TRAITS.get(sid, list(range(len(TEXTS))))
            n_open = rng.randint(3, 5)
            open_answers = []
            for k in range(n_open):
                # 75% on-profile, 25% noise — including the seeded PII cases.
                if rng.random() < 0.75:
                    raw = TEXTS[traits[rng.randrange(len(traits))]]
                else:
                    raw = TEXTS[rng.randrange(len(TEXTS))]
                result = redactor.apply(raw)
                for code, count in result.counts.items():
                    stats["redactions"][code] = stats["redactions"].get(code, 0) + count
                open_answers.append({
                    "ref": f"probe:seed{k}",
                    "competency_id": None,
                    "theme": rng.choice(["strengths", "opportunities"]),
                    # `raw`, not `result.text`. Storing the masked form here would
                    # make seeded rows the only ones in the database already
                    # scrubbed, so HR's verbatim view of them would show
                    # placeholders while real interviews showed sentences.
                    "text": raw,
                    "codes": result.codes,
                })

            store.save_submission(ballot_id, likert, open_answers, [], declined=declined)
            stats["ballots"] += 1
            stats["likert"] += len(likert)
            stats["declined"] += len(declined)
            stats["open"] += len(open_answers)

        # The subject rates themselves. Stored outside the ballot pool, so it
        # can never reach an aggregate.
        skew = SELF_SKEW.get(sid, 0.0)
        store.save_self_assessment(
            sid, ROUND_ID, {i.id: _score(centre + skew, rng) for i in all_items}
        )
        stats["self_assessments"] += 1

    return stats


def write_rater_roster() -> int:
    """inputs/raters-synthetic.csv — consumed by the identity side, never by this POC.

    It is generated here for completeness of the brief's "minimum inputs", and
    is deliberately NOT readable from the aggregation path: nothing in
    poc/aggregation imports it.
    """
    rng = random.Random(7)
    relationships = ["manager", "peer", "direct_report", "external_collaborator"]
    lines = ["rater_id,relationship_to_subject,cohort"]
    for subject in SUBJECTS:
        sid = subject.subject_id
        for b in range(PROFILE.get(sid, (4, 2.0))[0]):
            lines.append(f"R-{sid}-{b:02d},{rng.choice(relationships)},{subject.cohort}")
    path = config.INPUTS / "raters-synthetic.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines) - 1


if __name__ == "__main__":
    stats = seed()
    n = write_rater_roster()
    print(f"seeded {stats['ballots']} ballots · {stats['likert']} likert · "
          f"{stats['declined']} not-observed · {stats['open']} open answers · "
          f"{stats['self_assessments']} self-assessments")
    print(f"redactions applied: {stats['redactions']}")
    print(f"wrote inputs/raters-synthetic.csv ({n} raters)")
