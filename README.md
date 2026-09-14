# Conversational 360° — Proof of Concept

A working slice of the capstone described in [PLAN.md](PLAN.md): the **LangGraph
conversation engine** and the **aggregation + redaction pipeline**. Everything runs
locally, with no API key and no network, in under five seconds.

```bash
python3 -m venv .venv360 && ./.venv360/bin/pip install -r requirements.txt
./.venv360/bin/python -m poc.seed_ballots                       # synthetic round
./.venv360/bin/python -m poc.cli_report --freeze                # aggregate + report
./.venv360/bin/python -m poc.cli_interview --ballot me-01 --subject S1   # be a rater
./.venv360/bin/python -m poc.cli_diagram                        # graph + ERD diagrams
./.venv360/bin/python -m uvicorn app.main:app --reload           # the web UI  → :8000
./.venv360/bin/python -m pytest tests/ -q                       # 370 tests
```

---

## What this POC covers, and what it does not

The brief lists nine deliverables. This POC builds the two that carry the real
technical risk — the ones where a design mistake is expensive to discover late.

| In scope | Status |
|---|---|
| Conversational flow with consent, Likert battery, adaptive open probes, follow-ups | ✅ |
| "Not observed" opt-out, with an optional probe and coverage reporting | ✅ recorded, never scored |
| Save-and-resume across process restarts | ✅ SqliteSaver keyed on ballot_id |
| Guardrails — crisis signposting, HR-decision and clinical refusals | ✅ |
| Redaction, audience-scoped | ✅ 9 rule families, applied when rendering the subject's copy |
| Roll-up by item → competency → dimension → cohort | ✅ |
| Small-N suppression incl. dominance and complementary | ✅ 19 tests |
| Qualitative themes, paraphrase-only, min-N | ✅ with a leak check |
| Individual and cohort reports, CSV export | ✅ Markdown; PDF deferred |
| Round freeze with content digest | ✅ |

| Deliberately **not** in scope here | Why | Where it lands |
|---|---|---|
| FastAPI app, HTTP routing | ✅ **now built** — see [The web UI](#the-web-ui) | PLAN.md W1–W3 |
| Magic-link tokens, `identity.db` | Design is settled in PLAN.md §3; the POC asserts the *response* side of the boundary instead | PLAN.md W1 |
| Mobile web UI | ✅ **now built** — HTMX, mobile-first | PLAN.md W2 |
| RBAC, audit log, encryption at rest | Needs the web layer to be meaningful | PLAN.md W3–W4 |
| PDF rendering | Format concern; reports render from the same data | PLAN.md W3 |
| Admin console | Banks are already file-driven and hot-editable | PLAN.md W3 |

---

## Who sees what

The single most important thing to understand about this system, and the thing
that **changed after the second demo**.

| | HR / round administrators | The subject | Their manager |
|---|---|---|---|
| Free text | **as written** | names, contacts and role-disclosing phrases masked | same as the subject |
| Attribution | **reviewer named** | none | none |
| Themes | verbatim answers available | paraphrased, min-N, never quoted | same as the subject |
| Breakdown by role | **never** | never | never |

The client's clarification was that masking exists to protect the **subject's**
copy, not everyone's: HR runs the round and needs to see who reviewed whom and
what they wrote. That inverted an assumption this repo was built on, so three
things moved:

1. **Redaction moved from write time to render time.** `open_answer.text` holds
   the sentence as typed; `poc.reporting` masks it for `AUDIENCE_SUBJECT`.
2. **Attribution became a supported path.** `app.rounds.rater_labels()` resolves
   `ballot_id → reviewer` from the identity side and is passed *into* the report.
   `poc/` cannot attribute on its own — a caller holding both halves must hand it
   the mapping, so every attribution path is one somebody wrote deliberately.
3. **`consent.md` was rewritten.** It previously told reviewers that *"Nobody —
   not HR, not your manager — can look up what you personally said"*. That is no
   longer true, so the copy now says plainly that HR sees their answers with
   their name against them, **before** they write anything.
   `test_consent_copy_discloses_that_hr_can_attribute` fails if the code and the
   promise drift apart again.

**What survives the change**, and is still asserted by the build:

- `responses.db` carries no reviewer name, address, role or relationship in any
  column. That file alone discloses nobody.
- Relationship and cohort never travel with a response, so **no report, including
  HR's, can be broken down by role** — the part of the brief's anonymity
  requirement this change leaves intact.
- The subject's copy resolves none of it, and is the default rendering.

> **Note for the capstone submission.** The brief states *"Do not present
> individual attributable feedback to anyone; all responses must be fully
> anonymous in the first round"*, and mandates consent wording saying *"completely
> anonymous"*. The client's clarification contradicts both. This build follows the
> client and documents the divergence here rather than silently failing a stated
> requirement — see PLAN.md §9.

---

## Layout

```
conversational-360/
├── inputs/                    the instrument — editable without touching code
│   ├── README.md                  how to fill each file, and who owns it
│   ├── subjects.csv               WHO is reviewed — the one file you must edit
│   ├── competencies.json          C1–C6 verbatim from the client doc; B1–B4 drafted
│   ├── questions.json             probes, follow-up templates, probe policy
│   ├── reporting-rules.yaml       min_n, dominance, thresholds, retention
│   ├── consent.md                 legal copy shown before anything is asked
│   ├── name_gazetteer.txt         names the redactor removes on sight
│   └── raters-synthetic.csv       generated; belongs to the identity side
├── poc/
│   ├── banks.py  config.py  llm.py  store.py
│   ├── graph/     state · nodes · guardrails · redaction · build
│   ├── aggregation/  rollup · suppression · themes
│   ├── reporting.py
│   ├── session.py             the drive loop the CLI and the web share
│   ├── seed_ballots.py  cli_interview.py  cli_report.py
├── app/                       FastAPI + HTMX — the manual-test surface
│   ├── main.py  deps.py  rounds.py
│   ├── routers/   rater · selfassess · reports
│   ├── templates/ base · landing · chat · self · admin · report · invites
│   └── static/htmx.min.js     vendored; a CDN link would break the offline guarantee
├── Dockerfile  render.yaml    password-protected demo deployment (Blueprint)
├── DEPLOY.md                  step-by-step Render deploy
├── docs/                      generated by `poc.cli_diagram`
│   ├── graph.md                   compiled LangGraph flow (mermaid)
│   └── erd.md                     data model + which side of the boundary each table sits on
├── tests/                     370 tests, no network, no key
└── data/                      generated: responses.db, checkpoints.db, reports/
```

~2,400 lines of implementation, ~900 of tests.

---

## Part 1 — The conversation engine

### Shape

```
START ─▶ next_question ─┬─(pending)─▶ collect ─┬─▶ next_question   (loop)
                        │                       └─▶ finish
                        └─(nothing left)───────────▶ finish ─▶ END
```

Two nodes do all the work, and the split between them is the design:

- **`next_question` decides what to ask.** Pure Python. It writes the question
  into state *before* the graph suspends, so the checkpoint holds the exact
  wording. A resumed session redisplays what it actually asked rather than
  regenerating something subtly different.
- **`collect` receives one answer**, runs guardrails, scores redaction without
  applying it, and asks the judge
  whether a follow-up is warranted. **Routing off that verdict is `if`, never the
  model.**

One rater turn per node invocation means every turn produces its own checkpoint.
That is what makes resume exact rather than approximate.

### What the model is allowed to do

| Task | Who decides | Why |
|---|---|---|
| Which competency gets an open probe, and when | Python (`plan_probes`) | Must be explainable to HR |
| Wording of a probe | LLM, from a bank template | Never invents a question with no bank entry |
| Is this answer specific enough | LLM → structured verdict | Judgement, not routing |
| Ask a follow-up or move on | Python, against a budget | Determines session length |
| Crisis / refusal detection | Python regex | A guardrail must not fail when the network does |
| Any score, band, or recommendation | **Nobody** | See "The boundary" below |

Run it with no `OPENAI_API_KEY` and it works: `C360_LLM_MODE` resolves to `stub`,
a deterministic implementation of the same interface. That is not only about
cost — it makes the tests hermetic and the demo reproducible.

**This is enforced, not assumed** (`tests/test_offline.py`). LangGraph itself
never calls a model — orchestration, checkpointing and interrupt/resume are pure
infrastructure, and the only model touchpoints in the POC live in `poc/llm.py`.
To prove that rather than assert it, two tests **sever the socket layer** —
`socket.socket`, `create_connection` and `getaddrinfo` all raise — and then run a
full interview and the whole aggregation pipeline through it. Anything reaching
for the network fails the test instead of hanging.

A separate autouse fixture pins the suite to stub mode regardless of what the
machine has exported. Without it the suite inherits a developer's
`OPENAI_API_KEY` and quietly starts making live calls: slow, non-deterministic,
billable, and it would invalidate every assertion that depends on the stub's
fixed wording. Verified by running the suite with a key set — same 149 passes.

Resolution is tested in both directions, so stub mode is a consequence of
configuration rather than a hardcoded fallback that would ignore a real key.

### Adaptive probing

Ten competencies × an open question each would be accurate and unusable;
completion friction is an evaluation criterion in its own right. So probes go
where the ratings carry signal:

| Rating pattern | Trigger | Example |
|---|---|---|
| mean ≤ 1.5 | `low` | *"What is one process they own that has stayed manual?"* |
| internal spread ≥ 1 | `mixed` | *"Your ratings were uneven. Which part holds up, which slips?"* |
| mean ≥ 2.7 | `high` | *"What have they simplified that the team still benefits from?"* |
| otherwise | `default` | |

Capped at 4 competencies, with **one slot reserved for the behavioural
dimension** — core competencies win on signal nearly every time, and without the
reservation the 40% dimension would only ever collect Likert data. That gap is
precisely what this project exists to close.

**A probe is asked immediately after the competency that triggered it**, not in
a block once the battery is over:

```
C1.1  C1.2  C1.3  →  probe:C1 "Your ratings on Delivery were uneven…"
C2.1  C2.2  C2.3
C3.1  C3.2  C3.3  →  probe:C3 "What kinds of decisions do they escalate…"
…
B4.1  B4.2        →  probe:B4
                     Q1  Q2  Q3  Q4   (the fixed qualitative block)
```

The ratings that produced the question are the last thing the rater answered, so
they can see what the follow-up is about instead of matching it back to
something twenty questions ago. The probe also carries its competency name —
*"Following up on Delivery"* — so the link is stated, not inferred.

The cost is that `plan_probes` can no longer rank all ten competencies against
each other: a competency has to be decided when its own ratings land, before the
ones below it exist. Two rules keep a late signal from finding the budget spent:

* A competency with **no signal at all** (every item rated the same middling 2)
  takes a slot only when there are too few competencies left to fill the budget
  anyway, so it can never crowd out a real signal that follows it.
* The **last free slot is held for the behavioural dimension** while any
  behavioural competency is still ahead in the bank.

What remains is that a `low` late in the bank can lose to a `mixed` early in it.
That is the trade accepted for the ordering, and it is bounded: the plan is
*prefix-stable*, so the same function returns the same plan whether it is called
once per competency during the interview or once over a finished ballot — which
is what lets a review-step edit re-plan without rebasing anything.

Follow-ups escalate rather than repeat: `too_short` → `needs_behaviour` →
`needs_example` → `needs_effect`. Asking for an outcome before there is even an
example produces a confusing question. Budget: 2 per probe, 8 per session.

### Guardrails, and a measured number

```
guardrail recall: 56/63 = 89%   (was 76%)
false positives on ordinary workplace language: 0   (was 10 of 21)
```

`tests/fixtures/guardrail_phrases.yaml` is the specification: 84 phrasings of how
people actually write these things, written to be read by HR and Legal rather
than by the regex. Phrasings go in there first; patterns are fixed second.

That file exists because of what it found. There were nineteen guardrail tests,
all passing, and none of them asked *"here are sixty ways people say this — how
many do we catch?"* The first measurement said 76%, and the gaps were systematic
rather than incidental:

* **Every crisis verb was present-tense only.** `\bkill (?:myself|me)\b` cannot
  match *"killing myself"*; `\bhurt myself\b` cannot match *"hurting myself"*;
  `\bself[- ]harm\b` cannot match *"self-harming"*, because the `\b` fails
  before "ing". Three of the most common phrasings for the most serious signal
  the system handles, and the failure was silent — no signposting, and the
  interview carried on asking about competencies.
* **`\bend (?:my|...)\b` allowed a bare "my"**, so *"end my involvement"* and
  *"end my secondment"* halted the interview and signposted EAP at someone
  discussing a project.
* **`COMPARE_NAMED` was compiled with `re.I`**, which makes `[A-Z][a-z]+` match
  any word at all. *"better than average"*, *"worse than last quarter"* and
  *"compared to previous reviews"* were refused and the rater's turn discarded.
  Comparative language is ordinary in 360 feedback, so this fired constantly.
  Fixed with `(?-i:[A-Z][a-z]+)` — the same two-tier reasoning the redactor uses,
  where requiring the capital separates a person from a thing.
* **`\bdiagnos(?:e|is|ed)\b`** refused *"she diagnosed the root cause"*, and
  **`health`** in the private-life list refused *"their health and safety
  write-up"*.

False positives are held to a stricter standard than false negatives here, and
not because they matter more in the abstract. A false refusal silently discards
the answer someone just wrote; a false crisis halts a working interview. Both
teach raters the system overreacts, which is how you end up with feedback nobody
says anything real in — the same reasoning that keeps "overwhelmed" out of the
crisis list.

**The known-miss list is the honest part.** Seven phrasings are recorded as
deliberately uncaught, each with a reason — indirect ideation (*"everyone would
be better off without me"*), euphemism, and Spanish, which the bilingual
instrument accepts but the English-only phrase list does not cover.
`test_known_misses_are_still_missed` asserts they *still* miss, so improving the
matcher fails the suite and forces the entry to be promoted. Without that, the
list of limitations quietly becomes a list of lies.

This is where a model-based check would earn its place — see the seam at
`llm_second_opinion`. The composition rule matters more than the model: OR, never
AND. A model may raise a verdict the patterns missed; it must never downgrade one
they caught, and a failed call must not clear one.

### Guardrails

```
$ ... should I fire them?
  I can't weigh in on promotion, pay, or performance decisions — this system is
  deliberately built not to produce them. […] a person makes those calls.
  → same question re-asked; the turn is not recorded
```

Crisis detection halts the interview, signposts to the EAP placeholder, and
**keeps the ratings already given** — someone in distress should not also lose
twenty minutes of work. A lower `distress` tier offers support without halting.

"Overwhelmed" alone is deliberately *not* a crisis trigger. It is ordinary
workplace language, and firing on it would train raters to self-censor.

### Save-and-resume

The rater's link is the resume handle; there is no login and no session table.

```
$ python -m poc.cli_interview --ballot resume-test --subject S2
  [4/26] …            ^C
$ python -m poc.cli_interview --ballot resume-test --subject S2
  … resuming ballot resume-test (stage: likert)
  [4/26]  Level of Autonomy — End-to-End Management
```

---

## Part 2 — Aggregation and redaction

### Redaction runs on the way *out*, for the subject's copy

Nine rule families: `EMAIL`, `PHONE`, `URL`, `HANDLE`, `TICKET`, `NAME`,
`SELF_ID`, `ROLE`, `TEAM_SIZE`.

```python
"Ana Torres missed FEAT-231; as their only direct report I chased her at ana@pluralit.com"
→ "[NAME] missed [TICKET]; [SELF_ID]I chased her at [EMAIL]"
```

**Where this runs changed after the second demo.** It used to run before
persistence and destroy the original. The client clarified that HR administers
the round and reads answers as written — masking exists to protect the *subject's*
copy, not everyone's — so the store now keeps the sentence and
`poc.reporting` masks it when rendering for `AUDIENCE_SUBJECT`. The rules did not
change; where they run did. See [Who sees what](#who-sees-what).

Decisions worth defending:

**The default audience is the protective one.** `individual_report()` renders the
subject's copy unless a caller explicitly asks for `AUDIENCE_HR`, so a future
caller that forgets produces a duller report rather than a disclosure.
`test_reporting_defaults_to_the_protective_audience` pins it.

**`collect` still scores redaction exactly once**, without applying it — that
code list is what the rater's pre-submit preview shows, and computing it at write
time is what stops the preview and the subject's report disagreeing.
`test_collect_scores_redaction_once_and_never_reaches_the_store` enforces it
against the parse tree.

**The two rule groups now have different owners.** `ROLE`, `SELF_ID` and
`TEAM_SIZE` stop the *subject* inferring who reviewed them — free text saying
*"as their manager"* re-introduces exactly what the schema prevents, and most PII
tooling would not catch it. `NAME`, `EMAIL`, `PHONE`, `URL`, `HANDLE` and
`TICKET` protect *third parties named in the feedback* — colleagues and customers
who never consented to anything. That second group is GDPR data minimisation and
is unaffected by who is allowed to see what.

**The bias is toward over-redaction.** A false positive costs a slightly duller
sentence; a false negative identifies a reviewer to the subject. Those are not
symmetric. The patterns still stop at clause boundaries so feedback stays usable.

**What the move costs, and what it buys.** A raw answer now reaches disk, where
before it could not — that is a real loss, bounded by the 12-month raw-retention
rule, which is now doing work rather than describing already-scrubbed rows. What
it buys: the mask is no longer frozen at write time. Add a name to the gazetteer
today and every past answer is masked in the subject's copy from the next render.
Previously that name stayed exposed forever in rows already written.

**Names match in two tiers**, because a roster and a dictionary overlap. Entries
an administrator wrote are matched case-insensitively — raters type into a chat
box, and `ana torres` must not slip through on capitalisation alone. Single
tokens the loader *inferred* by splitting a full name stay case-sensitive:
`torres`, `marcos`, `salas`, `blanco` and `tomas` are all ordinary Spanish
words, and matching those case-insensitively turns one sentence of Spanish
feedback into five false positives. Requiring the capital separates a reference
to a person from a reference to a thing. Particles (`de`, `la`, `van`) are never
derived at all. The deliberate consequence is that a bare lowercase surname is
not redacted — genuinely ambiguous, and the full-name form a rater is far more
likely to write is caught by the tier above.

### Suppression: three rules, run to a fixed point

```
cells: 312
  published       182
  MIN_N           124
  DOMINANCE         2
  COMPLEMENTARY     4
```

1. **MIN_N** — fewer than `min_n` distinct **rater** ballots. Counts *ballots*, not
   answers: one rater filling six items is one response. The self-assessment is
   not a ballot and never counts toward it.

2. **DOMINANCE** — one ballot supplied >50% of the answers behind a figure. A
   cell can clear min_n and still be one person's opinion if the others skipped
   most of its items.

   Worth knowing: **dominance cannot fire on a three-item competency at
   `min_n=4`** — three other ballots contributing one answer each cap the leader
   at exactly 50%. It is structurally a dimension- and overall-level rule. The
   seeded roster includes a partial-completion case (S4: one complete rater,
   three who answered two items) so the rule is actually exercised.

3. **COMPLEMENTARY** — the one most implementations miss. If a parent is
   published and exactly one child is suppressed, the hidden value is
   recoverable as the residual. Suppressing a *second* child is what protects the
   first.

   It runs to a fixed point, because sacrificing that second child can leave
   *its* sibling group with a lone suppression. Parents are processed strictly
   top-down, so a parent's own status settles before its children are judged —
   otherwise the outcome depends on dictionary ordering.

Reports render the *reason*, never a blank, so a reader can tell "too few
responses" from "no data":

```
| Delivery      | — *Suppressed: publishing this value would reveal a suppressed sibling* |
| Innovate…     | — *Suppressed: fewer than 2 contributing responses*                     |
| Subject Four  | — *Suppressed: one response accounts for more than 50% of this cell*    |
```

### Themes: paraphrase-only, enforced in code

Two protections, neither of which is a prompt instruction:

- **`min_ballots_per_theme = 3`** — a theme raised by one rater is a fingerprint.
- **No verbatim.** Every emitted paraphrase is checked against every source
  answer for a shared 6-word n-gram; anything that overlaps is dropped. Even with
  names stripped, a rater's own sentence identifies them by voice and by
  specifics only they would know. The check runs in both modes, so a model that
  ignores "paraphrase, do not quote" is caught by code rather than trusted.

`test_verbatim_output_is_dropped_even_when_the_threshold_is_met` feeds a
deliberately leaky client to prove it.

---

---

## The web UI

```bash
./.venv360/bin/python -m poc.seed_ballots            # once, for a round with data in it
./.venv360/bin/python -m uvicorn app.main:app --reload
open http://127.0.0.1:8000
```

`app/` wraps the engine in HTTP and adds no logic of its own — every figure on
every page comes from `poc/`. It exists so the flow can be *used* rather than
described, which is a different kind of evidence than a passing test.

### What each surface is for

| Surface | Path | Shows |
|---|---|---|
| Round | `/` | Every subject, their ballot count **against `min_n`**, self-assessment status: not started · in progress `3/26` · complete |
| Add a subject | `/admin` | Appends to `inputs/subjects.csv` and registers them, so a round can start from nothing |
| Rater chat | `/b/{ballot_id}` | The conversation. The URL is the resume handle; the review step is editable |
| Self-assessment | `/self/{subject_id}` | Likert-only form, paged by competency, saves per page, freely navigable |
| Dashboard | `/admin` | Suppression counts, report links, freeze, LLM-mode control, model activity |
| Reports | `/admin/subject/{id}`, `/admin/cohort/{c}` | The same Markdown `cli_report` writes |
| Exports | `/admin/aggregates.csv` | The same CSV |
| Ballot links | `/admin/invites` | Pre-minted links, who each was assigned to, and status: not opened · in progress `12/26` · submitted · frozen |

### Following completion, and who a link belongs to

A round is chased, not just collected: HR has to know who still owes a review.
A minted link can be **assigned an email address** at `/admin/invites`, either
when it is minted or later, and each row shows that address next to the link's
live status. `Completion` on `/admin` is the same question one level up — per
subject, rater responses against `min_n` and the self-assessment separately.

That address is also what attributes the reviewer's answers on the HR copy of the
report — see [Who sees what](#who-sees-what). It lives in `data/invites.json`,
the identity side of the boundary described in PLAN.md §3, and
`app.rounds.rater_labels()` is the only function that resolves it.

What the invite page still refuses to show is **answer content**. Attribution
belongs on a report somebody opened deliberately, not on the list HR leaves open
in a tab while chasing responses —
`test_the_invite_page_still_shows_no_answer_content` holds that line. And it still
refuses to distinguish a decline from an unopened link, because `consent.md`
promises that declining records nothing, or to single out a session halted by the
crisis guardrail.

### The self-assessment has three states

It has no submit button — the form saves per page and the subject may revise
until the round closes — so "done" cannot mean "they pressed the button". It
means every item has a value, which leaves a real middle state:

| | |
|---|---|
| `not started` | no rows at all |
| `in progress` | some items answered — shown with the count, e.g. `12/26` |
| `complete` | every item answered |

Previously this was a boolean, and someone who had answered half the form was
displayed identically to someone who had not opened it — which had HR chasing
people who were already most of the way through.

### The instrument is worded for whoever is reading it

Every item in `competencies.json` carries two phrasings, and so does every anchor
set:

| Read by | Field | Wording |
|---|---|---|
| Rater | `prompt`, `anchor_sets` | *"Does this person consistently meet agreed deadlines…?"* |
| Subject | `self_prompt`, `anchor_sets_self` | *"Do you consistently meet agreed deadlines…?"* |

The self-assessment is the one surface where the reader **is** the person being
assessed, and asking them a question written about somebody else reads as a form
built for a different purpose. This is an editorial pair in the bank rather than a
transformation at render time: a regex over someone's carefully worded instrument
produces grammatical wreckage on exactly the items that matter most.

`banks.Item.prompt_for(perspective)` falls back to the rater wording if
`self_prompt` is missing, so a bank edit degrades to a slightly-off question
rather than a broken page — and `test_every_item_carries_both_wordings` catches
the omission at edit time instead.

### "Not observed" — the item a rater has no basis to answer

A 360 asks peers, reports and managers about the same ten competencies, and no
single rater has seen all of them. Until this existed the instrument required a
number for every item, which does not produce missing data — it produces
**invented** data, and the invented value lands in the average looking exactly
like an observation.

The opt-out sits apart from the 1–3 buttons, not as a fourth point on the scale:

```
1  In development / needs guidance
2  Meets expectations
3  Reference / exceeds expectations
───────────────────────────────────────────────
Not observed — I have no basis to rate this
```

**It is recorded, never scored.** A decline goes to `state["declined"]` and the
`declined_item` table, never to `likert`. Every aggregate is built from `likert`,
so nothing a rater could not see can reach an average — `test_a_declined_item_is_recorded_but_never_scored`
and `test_declines_reach_the_store_and_never_the_likert_table` hold both halves.

**The walk treats rated and declined alike.** `next_question` advances on
*answered*, or a declined item would be handed back forever and the interview
could never reach review.

**It triggers a probe.** A competency with any decline gets the `not_observed`
probe instead of a score-pattern one — the useful question is no longer "why did
you rate it 2" but whether they saw anything at all. It is the only trigger that
fires on the *absence* of a rating, and the only one that can handle a fully
declined competency, where there are no values to take a mean of.

**That probe is genuinely optional.** It never earns a follow-up, and "nothing to
add" stores no answer — pressing someone for detail they have just told you they
lack is how you get invented evidence a second time. Skipped probes are tracked
in `probes_skipped` for the same reason declines are tracked: the stage decides
what to ask from what has been answered, and a probe that stores nothing would
otherwise repeat.

**Changing your mind works.** The review screen lists declined items and rating
one clears the decline.

**Declines are a finding, not a gap.** The individual report renders:

> ### Where reviewers had no basis to judge
> | Competency | Reviewers who could not assess |
> |---|---|
> | Delivery | 3 of 4 |

which is also the honest explanation for an `n` that is lower on one competency
than elsewhere in the same report — otherwise it reads as data loss.

#### What this fixed in the seed data

`poc/seed_ballots.py` produces partial ballots to exercise `DOMINANCE` and
`COMPLEMENTARY` suppression. Before the opt-out existed, those ballots were a
shape **the product could not generate** — the engine had no path to a submitted
ballot with fewer than 26 ratings, so two of the three suppression rules were
demonstrated on unreachable data. The seeder now emits real declines, and all
three rules still fire:

```
cells: 468
  published       387
  MIN_N           77
  DOMINANCE       2
  COMPLEMENTARY   2
```

### Sharing it: the demo deployment

`Dockerfile` deploys the POC as a password-protected demo so a stakeholder can
click a link instead of installing anything. **Step-by-step: [DEPLOY.md](DEPLOY.md).**

Deploy it as a Render **Web Service** with the Docker runtime — DEPLOY.md lists
every field and variable. `render.yaml` records the same configuration and works
as a Blueprint if you use that flow, but a manually created Web Service does not
read it, so keep the two in step by hand.

> **This repository is a deployment copy, not the source.** The canonical project
> lives in the client's GitLab (`gitlab.kdsoft.io`), which Render cannot connect
> to. Changes are made there and copied here — DEPLOY.md has the `rsync` command,
> which matters: a plain copy skips hidden files, and losing `.gitignore` would
> publish `data/invites.json` and its email addresses on the next commit.

```bash
docker build -t c360-demo .
docker run -p 8000:8000 -e C360_DEMO_USER=hr -e C360_DEMO_PASSWORD=... c360-demo
```

Four environment variables, all off by default so local work and the test suite
are untouched:

| Variable | Effect |
|---|---|
| `C360_DEMO_USER` / `C360_DEMO_PASSWORD` | HTTP Basic across the whole site. Both must be set, or the gate stays open — a half-configured gate must not lock the owner out |
| `C360_SEED_ON_BOOT` | Seeds the synthetic round **if the store is empty**. Free tiers have ephemeral disks, so a cold start would otherwise show "not enough responses" everywhere and read as a broken deploy |
| `C360_DEMO_BANNER` | Puts "every person here is synthetic" on every page |
| `C360_LLM_MODE=stub` | Pinned in the image. A demo must never depend on an API key, or bill per click |
| `C360_ROSTER_IN_DATA` | Keeps the subject list in `data/` beside the database. **Required whenever `data/` is on a persistent disk** — see below |

`/healthz` sits outside the gate, because a platform probe that gets a 401 reads
as a failed deploy.

**Free plan versus a persistent disk.** On Render's free plan the service sleeps
when idle and wakes with an empty filesystem, so minted links and anything else
entered are lost while the reseeded round makes the page look intact. A paid
instance with a disk mounted at `/app/data` fixes that — DEPLOY.md §1a.

The disk exposed a latent confidentiality bug. `/app/data` holds every file the
app writes except one: the subject list, which `/admin` appends to in `inputs/`.
With only `data/` persisted, a restart restores the bundled list while keeping the
database, so a subject added at runtime keeps their ballots but leaves the
redaction list — and their name appears unmasked in the subject's report.
`C360_ROSTER_IN_DATA` moves the live list onto the disk, copied once and never
overwritten, and `tests/test_persistent_roster.py` reproduces both the leak and
the fix. It is a switch rather than a path so the list cannot be pointed
somewhere off the disk and recreate the split.

**The banner is not decoration.** The seeded subjects have plausible human names —
deliberately, because the redaction rules have to be exercised against realistic
input — and a reader who assumes otherwise would draw conclusions about people
who do not exist.

#### Letting HR test against a real model

The demo boots in stub mode. To let HR see genuine model output, set
**`OPENAI_API_KEY`** in the Render dashboard (`render.yaml` marks it
`sync: false`, so Render prompts for it and it never enters the repo). HR then
clicks **Live** under Round controls on `/admin`.

**The key never reaches HR.** Calls are made server-side from the container, so
they exercise the key without ever holding it — nothing is sent to the browser,
and there is no field anywhere that displays it.

**Use a dedicated key, not your personal one.** Create a separate OpenAI
*project*, generate the key there, and set a **monthly budget cap** on that
project. The cap is the only hard stop that does not depend on this code being
correct, and a dedicated key can be revoked without disturbing your own work.

The exposure being bounded is small but not zero. One completed interview costs:

```
judge      16
followup    8
probe       4
─────────────
TOTAL      28 calls    (plus one theme-clustering call per report render)
```

At `gpt-4o-mini` with prompts this short that is a fraction of a cent, so a $5
cap is generous. What it protects against is not a demo — it is the password
being forwarded once too often.

Three things already limit it:

* **Stub is the boot default**, so the demo idles at zero cost.
* **The toggle does not survive a restart.** `set_llm_mode` persists to
  `data/web-settings.json`, which sits on an ephemeral disk, so a sleeping free
  tier wakes up in stub mode. Nobody can leave live mode switched on by accident.
* **The header says which mode is actually in force.** `llm_status()` reports
  `requested` and `effective` separately, so a missing or rejected key shows as
  degraded rather than looking like the model is being called when it is not.

What is *not* limited: anyone with the demo password can switch to Live and keep
clicking. The budget cap is what makes that survivable.

#### What this deployment is not

The password is one shared secret in front of a demo. **It is not the RBAC in
PLAN.md §3**: the role in the header is still a cookie the visitor sets
themselves, so anyone past the password can switch to HR / Admin and read every
report — including, since the demo-2 change, verbatim feedback attributed by
name. That is acceptable for a synthetic round and disqualifying for a real one.

Running an actual pilot needs real authentication, a hosting arrangement the
client is comfortable with under GDPR, and the two open decisions in PLAN.md
§9.6. None of those is a packaging problem.

#### A note on Python versions

`.venv360` runs 3.14; the image pins **3.13**, because hosts lag the newest
release. The suite passes on both — check it before changing the pin.

### The shared drive loop

`poc/session.py` holds the `Interview` class both the CLI and the web layer
drive. It was extracted from `cli_interview.py` rather than written fresh,
because the alternative is two surfaces that quietly disagree about what consent
accepts or when a follow-up fires.

```python
interview = Interview(ballot_id, subject_id)
turn = interview.open()      # start, or re-present the checkpointed question
turn = interview.send("2")   # one rater reply
```

`open()` is idempotent: calling it on an interview in progress re-displays the
question the checkpoint holds rather than advancing, which is what makes a
refresh, a back button, and reopening the tab tomorrow all do the same safe thing.

### Adding a subject writes two places, and rebuilds a third thing

HR can add a subject from `/admin`. The row goes to **`inputs/subjects.csv`**,
not only to the database, because that file is the roster of record:
`seed_ballots` re-reads it after wiping `responses.db`, so a database-only
subject would vanish on the next reseed. It is also upserted into `subject`,
which is what the reports join against.

The third step is the one that would fail silently. `nodes._redactor` is built
**once at import** from an `lru_cache`d roster read, so a subject added while the
server runs is unknown to it — and the first rater to name them has that name
written to the store in the clear. Nothing errors; the guarantee just stops
holding for exactly the people this feature creates. So `roster.add_subject`
clears the cache and the route calls `nodes.refresh_redactor()`, which rebinds
the module global. Rebinding rather than changing the call site is deliberate:
`test_identity_separation` asserts by AST that `collect` contains exactly one
`_redactor.apply(...)`, and that check should keep working.

`test_a_new_subjects_name_is_redacted_from_the_very_first_answer` adds a subject
over HTTP, runs an answer naming them through a real interview, and asserts the
name never reaches the response. Removing the rebuild fails it.

Ids are assigned automatically, reserving ids the store already knows — a
subject whose roster row was deleted still owns ballots, and reusing their id
would attach a new person to an old person's responses.

### Showing what actually produced each question

The mode badge says which client is *configured*. That is a weaker claim than it
looks, and the gap is where a demo goes quietly wrong:

| In live mode | What really happens |
|---|---|
| `probe`, `followup` | a model call — **or** the bank template, when the call raised and was swallowed |
| `judge_specificity` | a model call — **or** the deterministic filter, which answers short replies without calling |
| `cluster_themes` | **always** the fixed lexicon, by design |

A failed live call returns the bank template *byte-for-byte identical* to stub
output. Nothing raises, nothing differs on screen. So `poc/trace.py` records what
each call actually did — engine, outcome, latency — and three surfaces show it:

```
INFO    llm probe    openai model      1925ms [Delivery]
INFO    llm judge    openai heuristic     0ms (too short to be worth a call)
WARNING llm probe    openai fallback   1693ms [Delivery] (AuthenticationError)
```

* **Under each question in the chat**, HR only — `probe · gpt-4o-mini · 1189ms`.
  A rater sees a clean conversation; in a real round nobody should be told which
  model worded their question.
* **The uvicorn console**, always. A fallback logs at `WARNING`, so it is visible
  even when no one is looking at a page. `python -m poc.cli_interview --trace`
  turns the same output on for the terminal journey.
* **`/admin` → Model activity**, the last 40 calls with outcome and latency.

The record holds no answer text — only which operation ran, which engine served
it, and how long it took. `test_no_answer_text_is_ever_recorded` enforces that:
observability here must not quietly become a second copy of feedback sitting
outside the redaction path.

### Changing an answer before you submit

Both instruments are editable right up to submission, and neither is editable
afterwards. That line is not a preference — the review screen tells the rater
*"Once you submit you cannot edit these answers"*, and restates who will read
them: HR as written and attributed, the subject masked and unattributed. Saying
it twice, on consent and again at the moment of submitting, is deliberate — the
second time is when they are actually looking at what they wrote. `Interview`
refuses any edit on a submitted ballot, and a test asserts the stored row does
not move.

**The rater** gets an editable summary at the review step: all 26 ratings and
every written answer, each changeable in place. The Likert screen says so while
they are still answering — *"you can change any rating at the review step before
you submit"* — because the reassurance is worth more before someone hesitates
than after they go looking for it.

Edits go through `poc/session.py`, which patches the checkpoint with
`as_node="collect"` and then lets the graph run itself forward. That is what
keeps routing in one place: the patch looks to the checkpointer exactly like an
answer arriving, so the next invoke follows `collect`'s own edges into
`next_question`, and the flow decides what to ask next rather than the web layer
deciding for it.

**Editing a rating re-plans the probes**, which is the consequential part. Probes
go where the ratings carry signal, so a rating that drops from 2 to 1 can make a
competency the most important one to ask about; leaving the old plan would mean
the written evidence answers a question the ratings no longer pose. The cost is
that a rater at the review step can be sent back for a probe that did not exist a
moment ago.

That cost is bounded by the invariant `next_question` walks on: **never ask
something that already has an answer.** The walk goes down the bank asking for
any missing rating and then for any planned probe that has no answer yet, so
after a re-plan it stops only on genuinely new ground even though the new list
still names competencies already covered. Without it, correcting one rating
drags the rater back through every probe and all four fixed qualitative
questions. `test_editing_a_rating_replans_the_probes_and_asks_only_what_is_new`
pins exactly that: the answer set afterwards must differ by the one new probe.

The same walk is why an edit hands control back at the top of the bank rather
than computing a resume point. With the battery complete there is no rating left
to ask for, so it falls through to the first probe the new plan added, or to the
review — and `_guard_editable` refuses an edit until the battery *is* complete,
because an interleaved probe means being mid-battery is now a state the rater
can be in.

Edited free text is redacted through `nodes.redactor()` — the object `collect`
uses — so a reworded answer cannot reach the store under looser rules than the
one it replaced. What the rater edits is the *stored* answer, which for a probe
that drew a follow-up is the parent and its follow-ups merged. That is what the
report will quote, so it is the honest thing to show, even though it is not
word-for-word the turn they remember typing.

**The self-assessment** gets Back, Next and a jump list showing per-page
completion. Every navigation control is a submit button carrying `goto`, so
moving around always writes the current page first: navigating can never be the
thing that loses an answer. The radios are no longer `required`, because a Back
button that refuses to work on a half-filled page is worse than a partial save.

### What a ballot link is actually doing

`responses.db` cannot answer this, and for a long time the UI pretended it could.
A ballot row is created only in the `finish` node, only for a rater who
submitted — so `ballot.status` is `submitted` or `frozen` and never anything
else. Its `'open'` default is unreachable, which meant the invite list's "in
progress" branch and the round panel's `n_open` count were both dead code
returning zero forever. A rater twenty questions in showed as **not opened**,
indistinguishable from someone who never clicked.

The live state is in the checkpointer, keyed on the same `ballot_id`. Reading it
crosses no boundary a report does not — a checkpoint holds the same answers the
store does, and no reviewer identity column, exactly like the store.

| Shown | Means |
|---|---|
| `not opened` | no checkpoint, still on the consent screen, **or declined** |
| `in progress` | consent given, unfinished — with `12/26` progress |
| `submitted` | `finish` wrote it to the store |
| `frozen` | the round was frozen after submission |

**Three things are deliberately not distinguishable**, and each is a promise
rather than a simplification:

* **Declining reads as never-opened.** `consent.md` says *"Declining closes the
  session and records nothing."* A page that lets HR tell those apart makes that
  false.
* **Pre-consent sits in the same bucket** — otherwise opening a link would show
  "in progress", and declining would visibly move it back, so anyone watching the
  page would learn a decline had happened.
* **A crisis halt reads as "in progress"** like any other unfinished session.
  `guardrail_event` stores a code and never the text precisely so distress cannot
  be attributed to a person; labelling it per link would undo that.

All three are tested, because each is the kind of thing a later "improvement"
would helpfully break.

One implementation note worth keeping: `ballot_states` drains the checkpointer's
listing before asking for any state. `list()` yields from an open cursor on the
same sqlite connection `get_state` uses, and interleaving the two hangs rather
than fails.

### Three decisions worth defending

**The echo is verbatim, and says what will happen to it.** Type *"Ana Torres
missed FEAT-231"* and the bubble comes back as you wrote it, with a line saying
those details will be removed from the copy the subject receives. Before the
masking move it echoed *"[NAME] missed [TICKET]"* and claimed the details were
"removed before saving" — which became false the moment the store started keeping
the original, and false in the one direction that matters, to the one person who
cannot check. The warning still comes from `nodes.redactor()`, so the bubble
cannot promise a removal the subject's report will not make.

**The subject panel shows counts against `min_n`.** At `min_n = 2` a subject with
one response renders *"Not enough responses to report"*, which is correct and
looks exactly like a bug. Showing `1 / 2` next to it turns an apparent failure
into a visible rule.

**Offline is the default, and one control says which mode is on.** Clicking
through 26 Likert items should not cost anything. The control on `/admin` writes
the choice to `data/web-settings.json`, and a middleware applies it to
`C360_LLM_MODE` at the start of every request — `llm.get_llm()` re-reads the
environment on every call, so there is nothing to invalidate. Asking for `openai`
with no key says so rather than silently serving stub output.

It is three buttons rather than a dropdown with an Apply step, and that is the
interaction bug worth naming. A `<select>` plus a separate submit can display a
mode that has not been applied yet, so the control and the header disagree until
you press Apply — and a control showing a setting that is not in force is
indistinguishable from a control that does not work. One button per mode, applied
on click, removes the pending state entirely. Exactly one is marked ACTIVE, and a
status line underneath names what `poc.llm` will actually hand out — which is not
the same as what was chosen for `auto`, or for `openai` with no key.

The file matters too. The first version set the environment variable once at import,
which meant the toggle reverted to stub on every `--reload` and every restart:
the setting lived only in the memory of a process uvicorn replaces whenever a
file is saved, and it reverted *silently*, which is the part that made it look
like the toggle did nothing. Applying it per request rather than at render time
is the other half — a route calls the model inside its handler, which runs before
any template is rendered.

`tests/test_web.py` isolates that file per test. Without it the suite would
inherit whatever mode was last picked in a browser, which is the same
ambient-configuration failure `conftest.force_stub_mode` already guards against,
arriving by a different route.

### What it deliberately is not

| Not built | Why | Where it lands |
|---|---|---|
| `identity.db`, magic-link token exchange | `data/invites.json` does the identity side's job in file form: it maps subject → link → assigned reviewer, and is what `rater_labels()` resolves for the HR copy. What is deferred is the token exchange and hashing, not the concept | PLAN.md W1 |
| Real auth | The role is a cookie, labelled as a mock in the header. `require_hr` is a real seam — no route reads a role itself | PLAN.md W3 |
| Audit log | Needs a table, and `test_every_table_is_classified` should be the thing that makes adding one deliberate | PLAN.md W3 |
| Concurrency | Single-process SQLite, unchanged | — |

### What the web tests prove

```
tests/test_web.py   27   full interview over HTTP, resume in a fresh client,
                         idempotent reopen, guardrail refusal re-asks, redacted
                         echo, declined consent stores nothing, self-assessment
                         paging and resume, self never becomes a ballot,
                         RBAC gate, under-min_n renders its reason, CSV export,
                         freeze digest, the LLM control reaching `get_llm()`,
                         surviving a restart, marking exactly one mode active,
                         and degrading honestly with no key; adding a subject
                         with its redaction rebuild; two structural guards
```

The structural two are the ones that matter in a year. `test_rater_router_cannot_reach_the_reporting_side`
checks by AST that the rater surface cannot import the aggregation or reporting
layer — the same standard `test_identity_separation.py` already holds
`poc/aggregation` to, applied to the surface that now sits in the request path.
`test_every_admin_route_is_role_gated` asserts the gate lives on the router, so a
report route added later inherits it and cannot forget.

---

## Diagrams

```bash
python -m poc.cli_diagram      # writes docs/graph.md and docs/erd.md
```

Both are **derived, never hand-drawn**: the graph comes off the compiled
LangGraph app, the ERD off a freshly initialised SQLite schema. A hand-drawn
diagram is wrong the moment someone adds a column; these are wrong only if the
code is.

Mermaid is emitted as text rather than PNG deliberately — `draw_mermaid_png()`
needs headless Chromium or a call to mermaid.ink, and the offline guarantee is
worth more than a rendered image. GitHub and VS Code render the fences natively.

`docs/erd.md` also classifies every table as pseudonymous, attributable-by-
design, or operational, and states where reviewer identity actually lives and
which single function resolves it. `test_every_table_is_classified` fails if a new table
appears without that decision being made — in a project whose central claim is
structural identity separation, that is exactly the change that should stop the
build.

## The self-assessment

The client's model treats it as a perspective, not a vote: *"poniendo foco en la
autoevaluación y la evaluación del reporte directo"* — it informs the development
conversation and is explicitly **excluded from the average**.

It is stored in its own table, not as a ballot, for two reasons. Semantically it
is a different instrument use. Structurally, flagging one ballot in a pool of two
or three as "the self one" would identify the remaining raters by elimination —
so keeping it separate means the anonymous pool never carries a role marker at
all. That table is deliberately attributable: it holds the subject's own answers
about themselves.

Reports show it as a comparison, never folded into a figure:

```
| Competency | Self | Raters | Gap                          |
| Delivery   | 2.67 | 1.78   | **+0.89** self rates higher  |

**Widest divergence: Delivery** — the subject sees more strength here than
raters do (+0.89). Worth opening the conversation with.
```

Where the rater side is suppressed the self figure still prints: the subject
already knows their own answer, so showing it reveals nothing about anyone.

## Round size, and an honest limit

The client's operating floor is **three evaluations per subject including the
self-assessment** — so two anonymous raters, one of whom their model makes the
direct manager. `min_n` is set to `2` to match that.

This is in tension with the brief, which requires round 1 to be "fully anonymous"
with no attributable feedback shown to anyone. The tension is recorded rather
than papered over:

- The **structural** guarantee is now narrower than it was: no reviewer identity
  is stored in any column of `responses.db`, but HR can resolve one by joining
  the identity side. See [Who sees what](#who-sees-what) — that is a client
  decision taken after the second demo, not a property that eroded.
- The **inferential** protection is not available at n=2. A subject who knows who
  was asked can often work out who said what.

So `consent.md` promises *confidential from the subject*, not *anonymous*, says
plainly that HR can attribute, and warns that in a small group an individual view
may be inferable by the subject anyway. Both reports carry the same
warning. `test_consent_copy_matches_the_configured_threshold` fails if the copy
and the config drift apart — the threshold is a Legal-facing promise, not just a
number.

Raising `min_n` to 3 restores a meaningful threshold and requires the client to
move to four evaluations per subject. That is a client decision, not a config
change.

**Below the threshold, reports degrade rather than blank out.** A page of dashes
invites a manager to read meaning into the pattern of what is hidden, so the
report says what happened instead, and keeps what is still usable:

```
## Not enough responses to report

Priya Raman received **1** rater response this round, below the threshold of 2…
This is not a finding about Priya Raman. It is a fact about the round.
```

followed by the self-assessment, cohort context, and the commitments table.

## The boundary

The client's current process derives salary-band position from these numbers.
The brief forbids encoding that in software. This POC resolves the conflict
explicitly rather than quietly:

> This report describes what raters observed. It does not produce a performance
> score, a seniority level, or a promotion recommendation, and it must not be
> used as one. Bands are context for a conversation. Decisions are made by people.

No code path produces an overall verdict, a ranking, or a promotion signal.
Asking the assistant for one is a refusal with a stated reason, not a deflection.

---

## Identity separation

**The boundary is one-directional.** Subjects are named — a development report is
useless if you cannot tell whose it is, and the subject consented to being
assessed. Reviewers are named to HR and to nobody else: their identity lives on
the identity side and is resolved only when rendering the HR copy. Both reports
say which is which on the page, because a reader who assumes the whole document
is anonymous will misread every figure in it. `subject.role` is a column;
`relationship_to_subject` is in `FORBIDDEN_COLUMNS`, so even the HR copy cannot
be broken down by role. Both tested.

The full design is PLAN.md §3. The POC builds the **response side** and asserts
its properties:

- No table in `responses.db` carries a rater column — checked against the live
  schema, so adding one later fails the build rather than leaking quietly.
- `cohort` hangs off the **subject**, never the ballot. A subject's team is not a
  secret; a rater's team identifies them when cohorts are small.
- `guardrail_event` stores a code and a timestamp. Never the text that triggered it.
- Nothing in `poc/aggregation/` imports the store or reads the rater roster —
  checked via AST, so a mention in a docstring does not fail the build and a real
  call cannot pass it.

---

## What the tests actually prove

```
tests/test_suppression.py          19   min_n boundaries, dominance, complementary,
                                        cascade to fixed point, no value in a suppressed export
tests/test_graph_flow.py           21   consent gate, decline, crisis halt, refusal re-ask,
                                        follow-up budget, redaction before state, bad input
tests/test_guardrails.py           19   crisis, refusals, distress tier, false-positive guards
tests/test_guardrail_recall.py     86   84 real phrasings: 89% recall, zero false
                                        positives, and known misses asserted to
                                        stay missed so the limits stay honest
tests/test_redaction.py            29   each rule family, clean text untouched, clause
                                        boundaries, name-casing tiers, Spanish false positives
tests/test_identity_separation.py  12   schema, AST boundary checks,
                                        named-subject / anonymous-rater asymmetry
tests/test_themes.py                8   threshold, verbatim leak, cap
tests/test_offline.py               7   mode resolution both ways, full interview
                                        and aggregation with sockets severed
tests/test_roster.py                9   subject roster validation, redaction merge
tests/test_self_assessment.py      16   exclusion from aggregates, gap rendering,
                                        graceful degradation, consent/config drift
tests/test_diagrams.py              9   every table classified, ERD covers the schema
tests/test_web.py                  46   the web layer over HTTP, the LLM-mode control
                                        reaching get_llm() and surviving a restart,
                                        adding a subject incl. the redaction rebuild
                                        and id reservation, plus two structural
                                        guards and the RBAC gate
tests/test_trace.py                10   a fallback is never reported as a live call,
                                        short answers never reach the model, themes
                                        are recorded as the lexicon, no answer text
                                        is ever held, bounded buffer
                                   ──
                                  291   ~25s, network severed
```

Three of these caught real problems during the build. `evaluate` assumed the cell
dict key equalled `Cell.key`, and it processed parents in arbitrary order —
making complementary suppression depend on dictionary ordering. And the suite
originally inherited ambient environment variables, so on a machine with a key
exported it would have made live calls while still claiming to be hermetic. All
three are fixed.

---

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `OPENAI_API_KEY` | unset | Unset → stub mode |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `C360_LLM_MODE` | `auto` | `stub` forces offline even with a key |

`.env` in this directory is loaded at import with `override=False`, so a real
environment variable still wins and CI keeps control of its own settings.

Instrument and rules are data. Editing `inputs/reporting-rules.yaml` changes
suppression without a code change; editing `competencies.json` changes the
instrument. This is the brief's "Administrator must be able to manage the
question and competency bank" at POC scale.

**[`inputs/README.md`](inputs/README.md) documents every input file** — what it
answers, who owns it, and what changing each setting costs. `subjects.csv` is the
only one that must be edited before a round; it is also an addition to the
brief's input list, which specifies a rater roster but never a subject roster.

---

## Honest limits

- **Redaction is regex, not NER.** Deterministic, auditable, hermetic — and it
  will miss a name that is neither on the roster nor shaped like the two-capital
  heuristic, and a bare lowercase surname by design (see the two tiers above).
  Production needs a PII service behind the same `Redactor.apply` seam.
- **The gazetteer comes from the HRIS in production**, which makes it a list of
  every employee's name. The POC's copy is synthetic; the real one needs a
  thought about where it is stored before it is committed anywhere.
- **Theme clustering uses a fixed lexicon** in both modes. Deliberate: a stable
  theme vocabulary keeps reports comparable across rounds, which is what makes
  trend analysis possible later. Free-form clustering would drift.
- **Reports are Markdown.** PDF is a rendering concern, and WeasyPrint's system
  dependencies are a known Week-3 risk (PLAN.md §8).
- **Single-process SQLite**, no concurrency handling.
- **B1–B4 wording is a draft** and needs HR/Legal sign-off before any real round,
  per the brief's non-functional requirements.
- **The EAP link is a placeholder.** It is the one screen where a dead
  placeholder would matter most (PLAN.md §9, item 4).

---

## Next

`PLAN.md` Week 1–2 now reduce to wrapping this engine in FastAPI and building
the identity/invite side against the boundary the tests already enforce. The
Week-3 aggregation work is largely done; what remains there is PDF rendering,
the admin console, RBAC, and the audit log.
