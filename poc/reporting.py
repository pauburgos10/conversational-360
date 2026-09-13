"""Report rendering.

Two reports, both Markdown in the POC (PDF is a rendering concern deferred to
the full build). What matters here is not the format but the discipline:

* Every number is either published with its n, or replaced by its suppression
  reason. There is no silent blank.
* No overall score, no seniority level, no promotion recommendation appears
  anywhere. See PLAN.md section 2 — the client's current process derives band
  position from these numbers, and the brief forbids encoding that in software.
  The report gives a manager material for a conversation and stops there.
"""

from __future__ import annotations

from . import banks, config, roster
from .aggregation import rollup, suppression, themes as themes_mod
from .aggregation.rollup import (
    LEVEL_COMPETENCY, LEVEL_DIMENSION, LEVEL_OVERALL, SCOPE_COHORT, SCOPE_SUBJECT, Cell,
)
from .graph.redaction import Redactor

# Who is reading, which decides what they get to see.
#
# The client's clarification (demo 2, 2026-09-09) is that masking exists to
# protect the SUBJECT's copy, not HR's. HR runs the round; they need to know who
# reviewed whom and read the evidence as it was written. So the two audiences
# genuinely diverge, and the divergence is here rather than in the store:
#
#   AUDIENCE_SUBJECT  names and contact details masked, no attribution, themes
#                     paraphrased, small-N cells suppressed.
#   AUDIENCE_HR       free text as written, attributed to the rater the link was
#                     assigned to.
#
# The default is SUBJECT on purpose. A caller that forgets to say who is reading
# gets the protective rendering, so the failure mode of a future bug is a duller
# report rather than a disclosure.
AUDIENCE_SUBJECT = "subject"
AUDIENCE_HR = "hr"

HR_EYES_ONLY = (
    "> **HR copy — do not forward to {name}.** This version attributes each answer to the "
    "reviewer it was assigned to and quotes it as written. The subject's copy of this "
    "report, at the same URL with `?audience=subject`, has names and contact details "
    "masked and carries no attribution. Sharing this version with the subject would "
    "disclose who said what, which reviewers were not told would happen."
)

UNASSIGNED_RATER = "unassigned link"


def _masker() -> Redactor:
    """Built per call, not cached, so a subject added mid-round is covered.

    `roster.redaction_names()` reads an `lru_cache`d roster that `add_subject`
    clears, so this is current as of the last roster write. Reports are not a hot
    path and a stale redactor here would mask a name it has never heard of —
    which is the one failure this whole module exists to prevent.
    """
    return Redactor(*roster.redaction_names())


def _open_records(round_data: dict, ballot_ids: set[str],
                  audience: str) -> list[tuple[str, str]]:
    """`(ballot_id, text)` pairs for theme extraction, masked unless HR is reading.

    Themes are paraphrased and verbatim-checked downstream either way. Masking the
    input as well is belt-and-braces for the subject's copy: a paraphrase built
    from text that still contains "Ana Torres" can surface the name even when no
    six-word run is shared with the source.
    """
    rows = [(o["ballot_id"], o["text"]) for o in round_data["open"]
            if o["ballot_id"] in ballot_ids]
    if audience == AUDIENCE_HR:
        return rows
    redactor = _masker()
    return [(bid, redactor.apply(text).text) for bid, text in rows]


def _attributed_answers(round_data: dict, ballot_ids: set[str],
                        raters: dict[str, str] | None) -> list[str]:
    """The HR-only section: every answer, as written, under the reviewer's name.

    `raters` maps ballot_id to whatever the identity side knows — an email in this
    POC, a rater record in the full build. A ballot with no entry is shown as an
    unassigned link rather than omitted: HR needs to see that a response arrived
    from a link nobody was recorded against, which is a distribution problem worth
    surfacing rather than hiding.
    """
    raters = raters or {}
    by_ballot: dict[str, list[dict]] = {}
    for answer in round_data["open"]:
        if answer["ballot_id"] in ballot_ids:
            by_ballot.setdefault(answer["ballot_id"], []).append(answer)

    if not by_ballot:
        return []

    out = ["## Responses by reviewer", "",
           "*Verbatim, attributed. This section does not appear in the subject's copy.*", ""]
    for ballot_id in sorted(by_ballot, key=lambda b: (raters.get(b, "~"), b)):
        who = raters.get(ballot_id) or UNASSIGNED_RATER
        out += [f"### {who}", "", f"*Link `{ballot_id}`*", ""]
        for answer in sorted(by_ballot[ballot_id], key=lambda a: a["ref"]):
            label = answer.get("competency_id") or answer["ref"]
            out.append(f"- **{label}** — {answer['text']}")
        out.append("")
    return out

# Two different people appear in a 360 report and only one of them is protected
# *in this copy*. Saying so on the page is not decoration: a reader who assumes
# the whole document is anonymous will misread every figure in it.
#
# The wording changed with the client's demo-2 clarification. It used to claim
# rater identities were "never stored beside their answers", which is no longer
# true of the system as a whole — HR can attribute. What remains true, and is all
# this note now claims, is that *this copy* does not attribute.
ANONYMITY_NOTE = (
    "> **Who is named here.** {name} is identified because this report exists for "
    "their development conversation. The people who gave the feedback are not named "
    "**in this copy**: responses are not broken down by role, names and contact "
    "details are masked, and open answers are paraphrased rather than quoted.\n"
    ">\n"
    "> **Confidential is not anonymous.** Reviewers are known to HR, who administer "
    "the round and can see who was asked and what they wrote. What this copy does is "
    "keep that out of the development conversation.\n"
    ">\n"
    "> **And confidential is not unidentifiable.** This round publishes figures built "
    "from as few as {min_n} responses. This copy does not reveal who said what, but at "
    "that scale the subject may be able to infer it from who they know was asked. "
    "Handle this document accordingly, and do not press for attribution."
)

INSUFFICIENT_NOTE = (
    "## Not enough responses to report\n\n"
    "{name} received **{n}** rater {word} this round, below the threshold of {min_n} "
    "needed to publish a figure. Nothing about the ratings is shown below — with that "
    "few responses, any average would be a single person's view wearing the appearance "
    "of a group's.\n\n"
    "This is not a finding about {name}. It is a fact about the round: it means too few "
    "people were asked, or too few replied.\n\n"
    "**What to do:** extend the review window and invite more reviewers, then re-run. "
    "The self-assessment and cohort context below are still usable for the conversation "
    "in the meantime."
)

BOUNDARY_NOTE = (
    "> This report describes what raters observed. It does not produce a performance score, "
    "a seniority level, or a promotion recommendation, and it must not be used as one. "
    "Bands are context for a conversation. Decisions are made by people."
)

# Conversation openers are templates keyed by theme, not model output: a manager
# is about to say these words to a real person, so they get reviewed once, by HR,
# rather than regenerated differently every run.
OPENERS = {
    "needs_prompting": "Ask what gets in the way of closing things out without a reminder — is it clarity, priorities, or load?",
    "late_risk_signalling": "Agree together on what 'early warning' means in practice, and when a risk should surface.",
    "escalates_decisions": "Name two or three decisions they can make without checking, and agree to review how it went.",
    "single_point_of_failure": "Pick one process that only they can run today, and agree what handing it over would look like.",
    "narrow_scope": "Explore what they would need in order to see further than their own remit — context, access, or invitation.",
    "hard_under_pressure": "Ask how pressure lands on them, and what support would change the way it comes out.",
    "reliable_delivery": "Recognise this explicitly; reliability is often invisible until it stops.",
    "quality_of_work": "Ask what standard they hold themselves to, and whether it is sustainable.",
    "takes_ownership": "Explore where they would like more ownership, since this is clearly a strength.",
    "knowledge_transfer": "Ask what else they would document if they had the time to.",
    "automates_and_simplifies": "Ask what they would automate next if given a free week.",
    "business_context": "Ask what they think the team should be doing differently next quarter.",
    "customer_focus": "Ask where they think the customer experience is currently weakest.",
    "supports_peers": "Check this is not quietly costing them their own priorities.",
    "positive_energy": "Acknowledge it — and check it is not effortful in a way that is going unseen.",
}


def _fmt_cell(cell: Cell, mark: suppression.Mark, rules: dict) -> str:
    if mark.suppressed:
        return f"— *{mark.reason}*"
    mean = cell.mean
    return f"**{mean:.2f}** · {rollup.band_for(mean, rules)} · n={cell.n_ballots}"


def _next_steps() -> list[str]:
    return [
        "",
        "## Next steps and commitments",
        "",
        "*To be completed together during the development conversation.*",
        "",
        "| What we agreed | Owner | By when |",
        "|---|---|---|",
        "|  |  |  |",
        "|  |  |  |",
        "|  |  |  |",
        "",
    ]


def _self_section(subject_id: str, round_data: dict, cells: dict[str, Cell],
                   marks: dict[str, suppression.Mark], rules: dict,
                   bank: banks.Bank) -> list[str]:
    """Self-assessment beside the rater aggregate.

    The client's model is explicit that the self-assessment is a perspective for
    the conversation, not an input to the score — so it appears here and nowhere
    else in this report. Every figure above it is raters only.

    A gap is only shown where the rater side survived suppression. Where it did
    not, the self figure still appears: it is the subject's own answer and they
    already know it, so printing it reveals nothing about anyone.
    """
    self_ratings = round_data.get("self_assessments", {}).get(subject_id, {})
    out = ["", "## Self-assessment vs. raters", ""]

    if not self_ratings:
        out += ["*No self-assessment was submitted for this round.*", ""]
        return out

    out += [
        "*Excluded from every figure above. Shown here only as a point of comparison "
        "for the development conversation — a gap is a topic to explore, not a finding.*",
        "",
        "| Competency | Self | Raters | Gap |",
        "|---|---|---|---|",
    ]

    gaps: list[tuple[str, float]] = []
    for comp in bank.competencies:
        values = [self_ratings[i.id] for i in comp.items if i.id in self_ratings]
        if not values:
            continue
        self_mean = sum(values) / len(values)

        key = f"{SCOPE_SUBJECT}:{subject_id}|{LEVEL_COMPETENCY}:{comp.id}"
        cell = cells.get(key)
        if cell is None or marks[key].suppressed:
            out.append(f"| {comp.name} | {self_mean:.2f} | — | *not comparable* |")
            continue

        delta = self_mean - cell.mean
        gaps.append((comp.name, delta))
        if abs(delta) < 0.25:
            note = "aligned"
        elif delta > 0:
            note = f"**+{delta:.2f}** self rates higher"
        else:
            note = f"**{delta:.2f}** raters rate higher"
        out.append(f"| {comp.name} | {self_mean:.2f} | {cell.mean:.2f} | {note} |")

    if gaps:
        widest = max(gaps, key=lambda g: abs(g[1]))
        direction = ("sees more strength here than raters do"
                     if widest[1] > 0 else "is harder on themselves here than raters are")
        out += ["", f"**Widest divergence: {widest[0]}** — the subject {direction} "
                    f"({widest[1]:+.2f}). Worth opening the conversation with."]
    out.append("")
    return out


def _coverage_note(round_data: dict, ballot_ids: set[str],
                   bank: banks.Bank) -> list[str]:
    """Which competencies reviewers could not assess, and how many said so.

    This is signal, not a gap to apologise for. A competency several reviewers
    had no basis to judge says something about how visible that part of the
    person's work is — and it is the honest explanation for an `n` that is lower
    here than elsewhere in the same report, which otherwise reads as data loss.
    """
    declined = [d for d in round_data.get("declined", [])
                if d["ballot_id"] in ballot_ids]
    if not declined:
        return []

    # Count reviewers, not items: three declined items inside one competency is
    # one person saying "I cannot judge this", not three.
    by_comp: dict[str, set[str]] = {}
    for row in declined:
        try:
            comp_id = bank.item(row["item_id"]).competency_id
        except KeyError:
            continue          # an item retired from the bank mid-round
        by_comp.setdefault(comp_id, set()).add(row["ballot_id"])

    if not by_comp:
        return []

    n_ballots = len(ballot_ids)
    out = ["", "### Where reviewers had no basis to judge", "",
           "*Marked \"not observed\" rather than rated. These are excluded from the "
           "figures above — they are not low scores.*", "",
           "| Competency | Reviewers who could not assess |", "|---|---|"]
    for comp in bank.competencies:
        raters = by_comp.get(comp.id)
        if raters:
            out.append(f"| {comp.name} | {len(raters)} of {n_ballots} |")
    out.append("")
    return out


def _competency_table(cells: dict[str, Cell], marks: dict[str, suppression.Mark],
                      scope: str, scope_id: str, rules: dict, bank: banks.Bank) -> list[str]:
    lines = ["| Competency | Dimension | Result |", "|---|---|---|"]
    dim_label = {d["id"]: d["label"] for d in bank.dimensions}
    for comp in bank.competencies:
        key = f"{scope}:{scope_id}|{LEVEL_COMPETENCY}:{comp.id}"
        cell = cells.get(key)
        if cell is None:
            lines.append(f"| {comp.name} | {dim_label.get(comp.dimension)} | — *no responses* |")
            continue
        lines.append(f"| {comp.name} | {dim_label.get(comp.dimension)} | {_fmt_cell(cell, marks[key], rules)} |")
    return lines


def _cohort_context(cohort: str, cells: dict[str, Cell],
                    marks: dict[str, suppression.Mark], rules: dict,
                    bank: banks.Bank) -> list[str]:
    """Cohort figures, as an anchor when a subject's own are unpublishable."""
    out = ["", "## Cohort context", "",
           f"*Aggregated across the whole {cohort} cohort, so it clears the threshold "
           "where this individual's own figures do not.*", ""]
    out += _competency_table(cells, marks, SCOPE_COHORT, cohort, rules, bank)
    out.append("")
    return out


def individual_report(subject_id: str, round_data: dict, cells: dict[str, Cell],
                      marks: dict[str, suppression.Mark], rules: dict,
                      bank: banks.Bank | None = None,
                      audience: str = AUDIENCE_SUBJECT,
                      raters: dict[str, str] | None = None) -> str:
    """One subject's report, rendered for whoever is reading it.

    `audience` decides masking and attribution; `raters` maps ballot_id to the
    reviewer the link was assigned to and is used only when HR is reading.
    """
    bank = bank or banks.load_bank()
    subject = round_data["subjects"].get(subject_id, {})
    name = subject.get("display_name", subject_id)
    role = subject.get("role") or "role not recorded"
    cohort = subject.get("cohort", "—")

    ballots = [b["ballot_id"] for b in round_data["ballots"] if b["subject_id"] == subject_id]
    records = _open_records(round_data, set(ballots), audience)
    found, diag = themes_mod.extract(records, rules)

    strengths = [t for t in found if t.valence == "strength"]
    opportunities = [t for t in found if t.valence == "opportunity"]

    out: list[str] = [
        f"# Manager-Ready Development Report — {name}",
        "",
        f"**{role}** · {cohort} cohort  ",
        f"**Round:** {rules['round']['label']}  ",
        f"**Rater responses in the average:** {len(ballots)} "
        f"*(the self-assessment is not one of them)*  ",
        f"**Suppression:** min_n={rules['suppression']['min_n']}, "
        f"dominance>{int(rules['suppression']['dominance_max_share'] * 100)}%, "
        f"complementary={'on' if rules['suppression']['complementary'] else 'off'}",
        "",
        HR_EYES_ONLY.format(name=name) if audience == AUDIENCE_HR
        else ANONYMITY_NOTE.format(name=name, min_n=rules["suppression"]["min_n"]),
        "",
        BOUNDARY_NOTE,
        "",
        "## Where the ratings landed",
        "",
    ]
    # Graceful degradation: below the threshold, a page of dashes tells a manager
    # nothing and invites them to read meaning into the pattern of what is hidden.
    # Say plainly what happened instead, and give them what IS usable.
    min_n = int(rules["suppression"]["min_n"])
    if len(ballots) < min_n:
        out = out[:-2]  # drop the "Where the ratings landed" heading
        out += [INSUFFICIENT_NOTE.format(
            name=name, n=len(ballots), min_n=min_n,
            word="response" if len(ballots) == 1 else "responses",
        )]
        out += _self_section(subject_id, round_data, cells, marks, rules, bank)
        out += _cohort_context(cohort, cells, marks, rules, bank)
        out += _next_steps()
        # Below min_n no figure is published, but the answers themselves still
        # exist and HR is entitled to them — suppression protects the subject
        # from inferring a rater, which is not a constraint on the round's owner.
        if audience == AUDIENCE_HR:
            out += _attributed_answers(round_data, set(ballots), raters)
        return "\n".join(out)

    out += _competency_table(cells, marks, SCOPE_SUBJECT, subject_id, rules, bank)

    out += ["", "## Dimensions", "", "| Dimension | Result |", "|---|---|"]
    for dim in bank.dimensions:
        key = f"{SCOPE_SUBJECT}:{subject_id}|{LEVEL_DIMENSION}:{dim['id']}"
        cell = cells.get(key)
        value = _fmt_cell(cell, marks[key], rules) if cell else "— *no responses*"
        out.append(f"| {dim['label']} ({int(dim['weight'] * 100)}%) | {value} |")

    out += _coverage_note(round_data, set(ballots), bank)

    out += _self_section(subject_id, round_data, cells, marks, rules, bank)

    out += ["## Themes raters raised", "",
            f"*Paraphrased. Themes mentioned by fewer than {rules['themes']['min_ballots_per_theme']} "
            "raters are not reported, and no rater is quoted.*", ""]

    if strengths:
        out += ["### Working well", ""]
        out += [f"- **{t.label}** — {t.paraphrase} *(raised by {t.n})*" for t in strengths]
        out.append("")
    if opportunities:
        out += ["### Worth attention", ""]
        out += [f"- **{t.label}** — {t.paraphrase} *(raised by {t.n})*" for t in opportunities]
        out.append("")
    if not found:
        out += ["*No theme reached the reporting threshold in this round.*", ""]

    out += ["## Suggestions for the conversation", ""]
    openers = [OPENERS[t.id] for t in (opportunities + strengths) if t.id in OPENERS][:4]
    if openers:
        out += [f"{i}. {text}" for i, text in enumerate(openers, 1)]
    else:
        out.append("*Not enough reportable signal to suggest openers this round.*")

    out += _next_steps()
    if audience == AUDIENCE_HR:
        out += _attributed_answers(round_data, set(ballots), raters)
    out += [
        "---",
        f"*Theme diagnostics: {diag['clustered']} clustered, {diag['dropped_min_n']} below threshold, "
        f"{diag['dropped_verbatim']} dropped for verbatim overlap, {diag['reported']} reported.*",
    ]
    return "\n".join(out)


def cohort_report(cohort: str, round_data: dict, cells: dict[str, Cell],
                  marks: dict[str, suppression.Mark], rules: dict,
                  bank: banks.Bank | None = None,
                  audience: str = AUDIENCE_SUBJECT) -> str:
    bank = bank or banks.load_bank()
    subject_ids = [s for s, v in round_data["subjects"].items() if v["cohort"] == cohort]
    ballots = [b["ballot_id"] for b in round_data["ballots"] if b["subject_id"] in set(subject_ids)]
    records = _open_records(round_data, set(ballots), audience)
    found, diag = themes_mod.extract(records, rules)

    out: list[str] = [
        f"# Cohort Report — {cohort}",
        "",
        f"**Round:** {rules['round']['label']}  ",
        f"**Subjects:** {len(subject_ids)} · **Responses:** {len(ballots)}",
        "",
        "> This report names the people being reviewed. It does not, and cannot, identify "
        "who gave the feedback.",
        "",
        BOUNDARY_NOTE,
        "",
        "## Competency profile across the cohort",
        "",
    ]
    out += _competency_table(cells, marks, SCOPE_COHORT, cohort, rules, bank)

    overall_key = f"{SCOPE_COHORT}:{cohort}|{LEVEL_OVERALL}:all"
    overall = cells.get(overall_key)
    out += ["", "## Overall", ""]
    out.append(f"- Cohort overall: {_fmt_cell(overall, marks[overall_key], rules)}" if overall
               else "- Cohort overall: — *no responses*")

    out += ["", "## Per-subject spread", "",
            "*Subjects are named; the raters behind every figure are not.*", "",
            "| Subject | Role | Result |", "|---|---|---|"]
    for sid in sorted(subject_ids, key=lambda s: round_data["subjects"][s]["display_name"]):
        key = f"{SCOPE_SUBJECT}:{sid}|{LEVEL_OVERALL}:all"
        cell = cells.get(key)
        subject = round_data["subjects"][sid]
        result = _fmt_cell(cell, marks[key], rules) if cell else "— *no responses*"
        out.append(f"| {subject['display_name']} | {subject.get('role') or '—'} | {result} |")

    out += ["", "## Themes across the cohort", ""]
    if found:
        out += [f"- **{t.label}** ({t.valence}) — {t.paraphrase} *(raised by {t.n})*" for t in found]
    else:
        out.append("*No theme reached the reporting threshold in this round.*")

    out += ["", "## Trend", "",
            "*Insufficient history — trend analysis requires at least two completed rounds. "
            "This section renders from round 2 onward.*", "",
            "---",
            f"*Theme diagnostics: {diag['clustered']} clustered, {diag['dropped_min_n']} below threshold, "
            f"{diag['dropped_verbatim']} dropped for verbatim overlap, {diag['reported']} reported.*"]
    return "\n".join(out)


def aggregates_csv(cells: dict[str, Cell], marks: dict[str, suppression.Mark], rules: dict) -> str:
    """HR export. Suppressed cells export their reason, never their value."""
    rows = ["scope,scope_id,level,level_id,label,n_ballots,n_answers,mean,band,suppressed,reason"]
    for key in sorted(cells):
        cell = cells[key]
        mark = marks[key]
        if mark.suppressed:
            rows.append(
                f'{cell.scope},{cell.scope_id},{cell.level},{cell.level_id},"{cell.label}",'
                f'{cell.n_ballots},{cell.n_answers},,,TRUE,"{mark.reason}"'
            )
        else:
            mean = cell.mean
            rows.append(
                f'{cell.scope},{cell.scope_id},{cell.level},{cell.level_id},"{cell.label}",'
                f'{cell.n_ballots},{cell.n_answers},{mean:.4f},"{rollup.band_for(mean, rules)}",FALSE,'
            )
    return "\n".join(rows)
