# Input files

Everything the system needs to run a round lives here. None of it is in code, so
a round is configured by editing these files and re-running the pipeline.

| File | Answers | Owner | Edit before every round? |
|---|---|---|---|
| `subjects.csv` | **Who is being reviewed** | HR / Admin | **Yes** |
| `competencies.json` | What is being measured | HR, with Legal sign-off | Rarely |
| `questions.json` | How it is asked | HR, with Legal sign-off | Rarely |
| `reporting-rules.yaml` | What may be published | HR / Data Protection | Occasionally |
| `consent.md` | What reviewers agree to — **including that HR can attribute** | Legal | Rarely |
| `name_gazetteer.txt` | Names stripped from free text | HRIS export | Yes |
| `raters-synthetic.csv` | Who was invited | *generated* — see below | No |

Order to fill them in for a first run: **`subjects.csv` → `name_gazetteer.txt` →
`reporting-rules.yaml`**. The other three ship with working defaults.

---

## `subjects.csv` — the one file you must edit

Who the round is about. Subjects are **not** anonymous: their name and role
appear on every report, because a development conversation has to be
attributable to a person. Reviewers are masked in the subject's copy only —
HR sees them by name. See the repo README, "Who sees what".

```csv
subject_id,display_name,role,cohort
S1,Ana Torres,Talent Partner,talent
S4,Diego Salas,Business Process,operations
```

| Column | Notes |
|---|---|
| `subject_id` | Any stable string. Becomes the report filename and the ballot's only link to a person. Must be unique. |
| `display_name` | Shown in report headers. Also auto-added to the redaction list, so a name here can never be missed. |
| `role` | Free text. The client's list: Talent Partner, Sourcer, Legal & Immigration, Business Process, Comms, Admin & Finance Support. |
| `cohort` | The grouping for team reports. Blank becomes `unassigned`. |

**Cohort sizing is the decision that matters most.** Aggregation suppresses any
figure built from fewer than `min_n` **rater** responses, and the self-assessment
is not one of them. At the current `min_n: 2`, a subject needs at least two
reviewers besides themselves; below that their report degrades to an explanation
plus self-assessment and cohort context. Decide cohort boundaries and invite
counts together, not separately.

Missing file, missing column, duplicate id, or empty name all fail loudly at
load with a message naming the line.

## `competencies.json` — the instrument

Ten competencies: `C1`–`C6` ported verbatim from *Compensation Structure 2026*,
`B1`–`B4` drafted to give shape to the previously vague 40% bucket.

To add a competency: append to `competencies`, give it an `id`, a `dimension`
(`core` or `behavioural`), an `anchor_set` (`delivery`, `management_impact` or
`behavioural`), and its `items`. Add a matching entry to `open_probes` in
`questions.json` or it will collect Likert data only.

`"draft": true` marks wording that has not been through HR/Legal review. All of
`B1`–`B4` currently carry it.

### Two wordings per item

Every item carries **both** phrasings, because the same question is read by two
different people:

```json
{
  "id": "C1.1", "label": "Commitment Fulfilment",
  "prompt": "Does this person consistently meet agreed deadlines...?",
  "self_prompt": "Do you consistently meet agreed deadlines...?"
}
```

`prompt` is what a **rater** is asked — third person. `self_prompt` is what the
**subject** is asked on their own form — second person singular. `anchor_sets`
and `anchor_sets_self` mirror each other the same way, so the level descriptions
under a question are in the same voice as the question.

Adding an item without a `self_prompt` will not break the page — it falls back to
`prompt` — but the subject then reads a question written about somebody else.
`tests/test_web.py::test_every_item_carries_both_wordings` fails on the omission
so it is caught at edit time rather than in a round.

## `questions.json` — how it is asked

Two parts.

`open_probes` maps each competency to probe wording per trigger — `low`,
`mixed`, `high`, `not_observed`, `default`. The trigger is chosen from the
rater's own Likert pattern for that competency, so a weak score and a strong one
get different questions. `default` is the only required key.

**`not_observed` is the odd one out.** It fires on the *absence* of a rating —
when the rater marked one or more items in that competency as "not observed" —
and it outranks every score-based trigger. Two things follow for whoever writes
this copy:

* It must read as an invitation, not a demand. The rater has just said they have
  no basis to judge; the probe asks whether they saw anything anyway, and every
  entry says in so many words that *"nothing to add"* is a fine answer. The engine
  honours that literally: it stores no answer and never asks a follow-up.
* It is the only probe that can be reached with **no ratings at all** in that
  competency, so the wording must not refer to "your rating".

`probe_policy` controls session length: `max_probed_competencies` (4),
`max_followups_per_probe` (2), `max_followups_per_session` (8). Raising these
deepens the qualitative data and raises abandonment. They are the main lever on
completion friction.

`min_words` (12) is the same trade seen from the other end: an open answer
shorter than this is judged `too_short` and draws the follow-up of that name,
with no model call — the deterministic filter decides thin answers on its own.
Lower it and thinner answers reach the themes stage; raise it and raters are
asked to elaborate more often.

`qualitative_fixed` holds the client's four existing open questions, asked
verbatim after the probes.

## `reporting-rules.yaml` — what may be published

| Setting | Default | Effect of changing it |
|---|---|---|
| `suppression.min_n` | `2` | Counts **rater** ballots; the self-assessment never counts. Set to 2 to match the client's floor of three evaluations including self. See the tension note in the file. |
| `suppression.dominance_max_share` | `0.50` | Catches a cell that clears `min_n` but rests on one rater who answered far more items. |
| `suppression.complementary` | `true` | Off means a lone suppressed figure is recoverable by subtracting from its parent. Leave on. |
| `themes.min_ballots_per_theme` | `3` | A theme raised by one rater is a fingerprint. |
| `themes.allow_verbatim_quotes` | `false` | On lets a rater's own sentence reach a report. Round 1 requires `false`. |
| `bands` | 2.0 / 2.5 | Labels only. The brief says 2.5; the client doc uses 2.6 on Autonomy + Innovation. |

## `consent.md` — shown before anything is asked

Legal owns this. Declining ends the session and records nothing.

**It must match what the code does — in two places.**

*What HR can see.* Since demo 2, HR reads answers as written with the reviewer's
name against them. The copy has to say so **before** anyone writes anything, and
it does. If attribution is ever switched off again, this file changes with it;
`test_consent_copy_discloses_that_hr_can_attribute` fails if the disclosure is
removed while the code still attributes.

*What the subject can infer.* The copy tells reviewers what protection they get;
`min_n` decides what they actually get. At `min_n: 2` the copy says *confidential
from the subject*, not *anonymous*, and warns that in a small group the subject
may be able to infer who said what anyway. Raising `min_n` means revisiting this
file, and `test_consent_copy_matches_the_configured_threshold` fails if the two
drift apart.

> This file is a promise to a person who cannot verify it. Everything else in
> `inputs/` is configuration; this one is the reason the rest is allowed to
> collect anything.

**Before a real round, replace `[Link to EAP or Internal HR Contact for
Well-being]` with the actual contact.** It appears both here and in the crisis
guardrail, and it is the one screen where a dead placeholder would matter most.

## `name_gazetteer.txt` — names stripped from free text

One name per line, `#` for comments. In production, export it from the HRIS.

It should be a **superset** of `subjects.csv`: anyone who might be *mentioned* in
feedback, including colleagues and former staff who are not themselves being
reviewed. Subject names are merged in automatically, so you cannot leak one by
forgetting to list it here.

This list protects **third parties named in the feedback** — people who never
consented to appearing in it — so it matters regardless of who is allowed to see
what. Since masking moved to render time, adding a name here also masks it in
every *past* answer from the next render onward; previously a late addition came
too late for rows already written.

Matching runs in two tiers. Lines you write match case-insensitively, so
`ana torres` typed casually is caught. Single tokens the loader infers by
splitting a full name stay case-sensitive, because *torres*, *marcos*, *salas*,
*blanco* and *tomas* are ordinary Spanish words. Consequence: a bare lowercase
surname is not redacted.

> In production this file lists every employee by name. Think about where it is
> stored before committing it anywhere.

## `raters-synthetic.csv` — generated, and deliberately unused

Written by `python -m poc.seed_ballots` to satisfy the brief's input list.
It holds `rater_id, relationship_to_subject, cohort` — the identity side of the
boundary. **Nothing in the aggregation path may read it**, and
`test_aggregation_never_touches_the_store_or_the_roster` enforces that against
the parse tree. In the full build this belongs in `identity.db`, not here.

---

## Running a round

```bash
python -m poc.seed_ballots                      # reads subjects.csv, writes synthetic ballots
python -m poc.cli_interview --ballot b1 --subject S1   # one real conversation
python -m poc.cli_report --freeze               # close the window, aggregate, render
```

`--freeze` records an immutable digest of the dataset before aggregating. Reports
land in `data/reports/`.
