# Conversational 360° — proof of concept

360° leadership-feedback prototype: a LangGraph conversational interview, an
aggregation and redaction pipeline, and a FastAPI + HTMX UI. Capstone project
(Real-world Project #03) built for a client, Pluralit. `README.md` is the full
design record; `PLAN.md` holds the plan and open decisions. This file is the
short version a new session needs first.

## This repository

- **It is the working copy and what Render deploys**
  (`github.com/pauburgos10/conversational-360`, branch `main`).
- **Pushing to `main` redeploys the demo HR is using.** Work on a branch and merge
  when ready.
- The **client repo** (`gitlab.kdsoft.io`, project under `paula/conversational-360/`)
  is a delivery copy and is **behind** this one. It gets updated later — see
  `DEPLOY.md`, "Delivering to the client repository". Do not treat it as current.
- **Never edit** `Repos/cohort-03-sync-sessions/conversational-360` — an old,
  stale copy.

## Commands

```bash
python3 -m venv .venv360 && ./.venv360/bin/pip install -r requirements.txt
./.venv360/bin/python -m pytest tests/ -q                # 430 tests; offline, no API key
./.venv360/bin/python -m uvicorn app.main:app --reload   # web UI on :8000
./.venv360/bin/python -m poc.seed_ballots                # WIPES data/responses.db first
./.venv360/bin/python -m poc.cli_report --freeze         # aggregate, write data/reports/
./.venv360/bin/python -m poc.cli_diagram                 # regenerate docs/erd.md, docs/graph.md
```

The local venv runs Python 3.14; the Docker image pins 3.13. The suite passes on
both — check before changing the pin.

## Who sees what — the core design fact

This was inverted after the second client demo. Read `README.md` "Who sees what"
and `PLAN.md` §3.1 before touching reporting, redaction or consent.

- **HR** reads free text as written, with the reviewer named. **The subject and
  their manager** get a copy with names and contact details masked, nothing
  attributed, and themes paraphrased.
- **Redaction runs when a report is rendered**, keyed on audience, in
  `poc/reporting.py`. It does not run on write: `open_answer.text` stores the raw
  answer. `AUDIENCE_SUBJECT` is the default, so a caller that forgets fails closed.
- `responses.db` has **no reviewer identity column**. Attribution is a deliberate
  join through `app.rounds.rater_labels()` against `data/invites.json`; `poc/`
  never reads the invite file.
- No report, HR's included, breaks feedback down by role.
- `inputs/consent.md` tells reviewers HR can attribute. Code and copy must agree.
- This diverges from the capstone brief ("no attributable feedback to anyone") on
  the client's instruction — open decision, `PLAN.md` §9.6.

## The instrument mirrors the client's sections — do not regroup it

`inputs/competencies.json` follows the compensation document's evaluation
template ("Plantilla de Evaluación 360"):

| Section | `dimension` | Competencies |
|---|---|---|
| Sección I: Expertise Técnico | `delivery` 60% | C1 |
| Sección II A: Competencias Blandas & Valores | `soft_skills` 40% | C2–C6 |
| Sección II B: Evaluación Cualitativa | — | `qualitative_fixed` |

All six were once `core`, putting five of the client's soft-skill competencies in
the 60% dimension. Four invented competencies (B1–B4) were withdrawn at the same
time: they filled a gap that did not exist. `tests/test_instrument_alignment.py`
pins all of it. See PLAN.md §1.1 and README "The instrument follows the client's
own sections".

The **overall figure is weighted** 60/40 across the two dimensions, not pooled
across items — pooling gave the 60% dimension about 12% of the say. Its `values`
stay pooled so suppression still counts the answers underneath.

## Invariants that fail silently when broken

- **Persistent disk ⇒ `C360_ROSTER_IN_DATA=1`.** With `data/` on a disk (Render,
  `/app/data`), the subject list must move there too. It is the only file the app
  writes outside `data/`. Without the variable, a restart reverts it: subjects
  added through `/admin` keep their ballots but leave the redaction list, and
  their names appear **unmasked** in the subject's report.
- **"Not observed" is never a score.** Declines go to `state["declined"]` and the
  `declined_item` table, never `likert` — every aggregate reads `likert`.
- **"Answered" means rated or declined.** The Likert walk, the probe planner and
  the review-edit guard all count both; optional probes waved away are tracked in
  `probes_skipped`. Counting only `likert` stalls the interview or silently
  blocks the review step.
- **Probe-policy edits go to `data/probe_policy.json`, never `questions.json`**,
  which holds HR- and Legal-reviewed wording. `banks.load_bank` is `lru_cache`d:
  clear it after any write to what it reads.
- **Only settings the engine reads may be editable.** `trigger_priority` in
  `questions.json` is descriptive only; the real order lives in `nodes._trigger_for`.
- **Never type an engine number into a template or doc — derive it.** Trigger
  thresholds are constants in `poc/graph/nodes.py`; the closing-question count and
  the item total come from the bank. Hand-copied numbers have gone stale four
  times now — the item count alone broke eight tests when the instrument shrank
  from 26 items to 18.
- **Probe planning must leave no dimension with ratings only.** Two rules do it: a
  no-signal competency keeps its slot when it is its dimension's last chance, and
  the final slot is held for a dimension with nothing planned. Delivery is a
  dimension of one sitting first in bank order, so without the first rule the 60%
  dimension gets no written evidence. Do not key either rule on a dimension name.
- **Rater prompts carry `{subject}`**, filled with the subject's first name;
  `self_prompt` addresses the subject directly. A new item without the
  placeholder fails `tests/test_subject_name.py`.

## Tests that enforce the design — do not weaken them

`test_no_table_carries_rater_identity` ·
`test_aggregation_never_touches_the_store_or_the_roster` ·
`test_rater_router_cannot_reach_the_reporting_side` ·
`test_only_finish_writes_to_the_store` ·
`test_collect_scores_redaction_once_and_never_reaches_the_store` ·
`test_reporting_defaults_to_the_protective_audience` ·
`test_consent_copy_discloses_that_hr_can_attribute` ·
`test_every_table_is_classified` ·
`test_every_editable_setting_is_actually_read_by_the_engine`

A new table must be classified in `BOUNDARY` in `poc/cli_diagram.py`, then run
`poc.cli_diagram`.

## Environment variables

All off by default, so local work and the suite are unaffected.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Live model. Unset key ⇒ stub mode |
| `C360_LLM_MODE` | `stub` / `openai` / `auto` |
| `C360_DEMO_USER`, `C360_DEMO_PASSWORD` | HTTP Basic over the site; both or neither |
| `C360_DEMO_BANNER` | "Every person here is synthetic" banner |
| `C360_SEED_ON_BOOT` | Seed the synthetic round if the store is empty |
| `C360_ROSTER_IN_DATA` | Keep the subject list in `data/` — required with a disk |

Render configuration is in `DEPLOY.md`; `render.yaml` records the same settings.

## Traps already hit

- **Copying the project** with Finder or `cp -R src/*` skips dotfiles — that lost
  `.gitignore` once. Use `rsync -a`.
- **A copied `.venv360`** still points at the old path. Rebuild it.
- **`.env` holds a live OpenAI key.** Never commit, copy or ship it.
- **Seeding wipes `responses.db`.** Back it up if it holds anything worth keeping.
- **Render's free plan wipes `data/`** when the service sleeps, and the reseeded
  round makes the page look intact.
- **The role switcher is a cookie the visitor sets.** The demo password is not
  authentication; anyone past it can read every report.

## Conventions

- Comments explain *why*, including the failure a line prevents. Match that.
- Tests are named for the behaviour or regression they pin.
- Run the full suite before calling a change done.

## Open items

- `PLAN.md` §9.6: brief versus client on attribution, and re-consent for data
  collected under the previous consent copy.
- Sync this repo into the client repo — four things to settle, listed in `DEPLOY.md`.
- In the client repo's pushed history: the client compensation `.docx`
  (`9b385ad`) and `data/invites.json` (`a7e3fce`). Raise with its administrator
  before rewriting history.
