# Conversational 360° Leadership Feedback — Implementation Plan

**Capstone:** Real-world Project #03 (v1.0)
**Client reference process:** *Pluralit Core Team — Compensation Structure 2026*
**Author:** Paula Burgos · Cohort 03
**Mode:** Solo · **Window:** 4 weeks (2026-08-19 → 2026-09-16)
**Stack decision:** FastAPI + LangGraph + SQLite/SQLAlchemy + server-rendered HTML (HTMX), no SPA framework

---

## 1. Where the client is today

The compensation document is the *as-is* process. Reading it carefully changes what we have to build, so it is worth being precise.

| Aspect | Current state (Compensation Structure 2026) |
|---|---|
| Model | `Seniority = Delivery (60%) + Soft Skills & Pluralit Values (40%)` |
| Competencies | 6, each with 3 sub-questions → **18 Likert items** |
| Scale | 1–3, anchored: 1 Development · 2 Proficient · 3 Highly Developed. Separate anchor text for *Delivery* vs. the five *Management & Impact* competencies |
| Qualitative | 4 open questions (2 strengths, 2 areas of opportunity) |
| Instrument | A **Word table**, filled in manually |
| Perspectives | Self-assessment + direct manager primarily; peers/reports "when needed" |
| Cadence | Biannual — June and December 2026; bonus month 7 and 13; band position revisited each review |
| Interpretation | Manual heuristics: ~2.0 → maintain level; >2.6 in Autonomy + Innovation + qualitative signals ("I don't need to supervise them") → promotion candidate; low Decision-Making + "cutting under pressure" comments → performance alert |
| Population | Talent Partner, Sourcer, Legal & Immigration, Business Process, Comms, Admin & Finance Support |
| Output | "Once the 7 profiles are collected", HR/Manager decides promotion and salary-band position |

### What the capstone changes

| Gap | Today | Required |
|---|---|---|
| **Anonymity** | Fully attributable — a named manager rates a named person | Confidential **from the subject**, attributable to HR, **no breakdown by role** — revised after demo 2, see §3.1 |
| **Collection** | Static form, high friction, inconsistent wording | Guided conversational flow, adaptive follow-ups, save-and-resume |
| **Consent** | Implicit | Explicit consent screen, GDPR-aligned, external raters must consent to scope |
| **Decisioning** | Scores feed promotion and band position | **Business rule: no automated performance scores.** Software surfaces evidence; humans decide |
| **Aggregation** | Manual reading of ~7 forms | Roll-up by dimension and cohort, **small-N suppression**, CSV + PDF export |
| **Governance** | None documented | Audit log of aggregate access, encryption, RBAC, 12-month raw retention |
| **Behavioral competencies** | ~~The 40% "Soft Skills & Values" bucket is under-specified~~ | **Withdrawn — the premise was wrong. See §1.1** |
| **Language** | Docs exist in ES and EN | Multilingual is a stretch goal, but ES/EN is effectively the real requirement |

### 1.1 Revision — the dimensions were mapped wrongly, and B1–B4 withdrawn

Two errors, found on 2026-09-27 by re-reading the compensation document's own
evaluation template ("Plantilla de Evaluación 360") rather than its narrative.

**The template groups the competencies by section, and we had it wrong:**

| Section | Competencies | Weight |
|---|---|---|
| Sección I: Expertise Técnico | `I. Capacidad de Delivery` only | 60% |
| Sección II A: Competencias Blandas & Valores Pluralit | `II A.1` Autonomía · `II A.2` Decisiones · `II A.3` Innovación · `II A.4` Impacto Sistémico · `II A.5` Cliente | 40% |
| Sección II B: Evaluación Cualitativa | the four open questions | — |

All six were marked `core`, so C2–C6 sat in the 60% dimension when the client
puts them in the 40% one. Corrected: `delivery` holds C1; `soft_skills` holds
C2–C6. Each competency now carries its `section` from the template.

**The 40% bucket was never under-specified**, so the four behavioural
competencies this plan invented (B1–B4) were filling a gap that did not exist,
and diluted the client's five. They are removed. Sección II B — the qualitative
half of that dimension — is what the instrument actually pairs with the ratings,
and it was already implemented as `qualitative_fixed`.

**The 60/40 weights were never applied.** `weight` appeared once in the codebase,
as a label on a report table; the overall figure pooled every item equally, which
gave the 60% dimension about 12% of the say once Delivery was correctly reduced
to three items of eighteen. `rollup` now computes the overall as the weighted
combination of the two dimensions, renormalised over the dimensions that have
answers. Note the tension with §2: this is a composite figure, so it is reported
alongside its dimensions and bands, never as a verdict.

**The document contradicts itself** on C5's second item — the narrative says
*Entusiasmo y clima laboral*, the template says *Orientación a resultados*. We
follow the template, being the instrument evaluators fill in. Worth confirming
with HR, since the narrative version is the only item covering team climate.

`tests/test_instrument_alignment.py` pins all of the above.

### Reconciling the two competency lists (superseded by §1.1)

| # | Competency | Type | Source | Sub-items |
|---|---|---|---|---|
| C1 | Delivery | Sección I (60%) | Client | 3 (Commitment, Quality, Obstacle Mgmt) |
| C2 | Level of Autonomy | Sección II A (40%) | Client | 3 (End-to-end, Proactivity, Ownership) |
| C3 | Maturity in Decision-Making | Sección II A (40%) | Client | 3 (Quality, Independence, Impact analysis) |
| C4 | Ability to Innovate and Improve the System | Sección II A (40%) | Client | 3 (Simplification, Status quo, Knowledge transfer) |
| C5 | Systemic Impact on the Business | Sección II A (40%) | Client | 3 (Strategic vision, **Results orientation**, Influence) |
| C6 | Customer Orientation | Sección II A (40%) | Client | 3 (Needs ID, Value added, Collective success) |
| ~~B1~~ | ~~Ability to Work with Others~~ | Invented | Capstone | **removed — §1.1** |
| ~~B2~~ | ~~Ability to Develop Others~~ | Invented | Capstone | **removed — §1.1** |
| ~~B3~~ | ~~Willingness to Help Others~~ | Invented | Capstone | **removed — §1.1** |
| ~~B4~~ | ~~Constructive Problem Solving~~ | Invented | Capstone | **removed — §1.1** |

**Threshold conflict to resolve:** the capstone specifies `<2 needs guidance · 2–2.5 meets · >2.5 possible promotion/bonus`; the client doc uses `>2.6` on Autonomy + Innovation. Resolution: thresholds live in `reporting-rules.yaml`, default to the capstone's 2.5, and the report renders them as **bands with labels, never as a verdict**.

---

## 2. The line we will not cross

The client's current process uses these scores to set salary band position. The capstone forbids encoding HR decisions in software. These are reconciled by an explicit product boundary, stated in the README, in the ethics note, and in the report footer:

> The system computes descriptive statistics and surfaces qualitative themes. It does **not** output a seniority level, a promotion recommendation, a band position, or a pass/fail. Threshold bands are shown as context for a human conversation. Salary-band mapping stays entirely outside this system.

Anything that would look like an automated verdict — a "promotion score", a ranked leaderboard, a red/green traffic light on a person — is out of scope by design, not by omission.

---

## 3. Architecture

```
                    ┌──────────────────── TRUST BOUNDARY: Rater ────────────────────┐
  magic link  ──▶   │  /r/{invite_token}  →  redeem  →  /b/{ballot_token}           │
                    │  Mobile web UI (HTML + HTMX, no JS framework)                  │
                    └───────────────────────────┬───────────────────────────────────┘
                                                │ HTTPS
                    ┌───────────────────────────▼───────────────────────────────────┐
                    │  FastAPI                                                       │
                    │   ├─ rater router     (consent, conversation, resume, submit)  │
                    │   ├─ admin router     (banks, window, freeze, exports)  [RBAC] │
                    │   └─ report router    (aggregates, PDF)          [RBAC+AUDIT]  │
                    └───────┬───────────────────────────────┬───────────────────────┘
                            │                               │
              ┌─────────────▼──────────────┐   ┌────────────▼─────────────┐
              │ LangGraph conversation     │   │ Aggregation + redaction  │
              │  engine (SqliteSaver)      │   │  small-N · theme cluster │
              │  guardrails · PII scrub    │   │  CSV · PDF renderer      │
              └─────────────┬──────────────┘   └────────────┬─────────────┘
                            │                               │
       ┌────────────────────▼───────────────────────────────▼────────────────────┐
       │  ══════════════ IDENTITY SEPARATION (the core design) ══════════════    │
       │                                                                          │
       │   identity.db                     │        responses.db                  │
       │   ─────────────                   │        ────────────                  │
       │   invite(token_hash, rater_id,    │  ✂     ballot(ballot_id, subject_id,  │
       │          subject_id,              │        round_id, state, submitted)    │
       │          relationship, cohort,    │        answer(ballot_id, item_id,     │
       │          redeemed:bool)           │               likert | text_redacted) │
       │                                   │        transcript(ballot_id, turns)   │
       │        NO FOREIGN KEY CROSSES THIS LINE                                  │
       └──────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Revision after demo 2 — attribution is a feature, not a leak

The client clarified on 2026-09-09 that **HR needs to see which reviewer evaluated
which subject, and what they wrote.** Masking exists to protect the copy the
*subject* receives; it was never meant to hide contributors from the people
running the round.

That inverts the assumption the rest of this section was written on, so what
follows is now the design for the **subject-facing** guarantee, not a
system-wide one. Three consequences:

1. **Redaction moved from write time to render time.** `open_answer.text` keeps
   the sentence as written; `poc.reporting` masks it for `AUDIENCE_SUBJECT` and
   not for `AUDIENCE_HR`. The rule set did not change.
2. **`ballot_id` is now deliberately resolvable.** The identity side maps it to
   the assigned reviewer, and `app.rounds.rater_labels()` performs the join for
   the HR copy only. Steps 2–4 below describe unlinkability that this build does
   not implement and should not claim.
3. **`consent.md` was rewritten before any of it shipped.** Reviewers are told,
   before writing, that HR sees their answers with their name attached.

**What survives, and is still enforced:** no reviewer identity column in
`responses.db`; `relationship_to_subject` still never leaves the identity side,
so no report — including HR's — can be broken down by role; the subject's copy is
the default rendering.

**Conflict with the brief, recorded not resolved.** The capstone brief requires
*"Do not present individual attributable feedback to anyone"* and mandates consent
wording saying *"completely anonymous"*. The client's instruction contradicts
both. This build follows the client and documents the divergence — see §9.6.

### How unlinkability was specified (superseded in part — see §3.1)

1. Admin generates invites. `identity.db` holds `rater_id → subject_id`, relationship, cohort. The raw token is never stored — only `sha256(token)`.
2. Rater opens `/r/{token}`. Server verifies the hash, mints a **fresh random `ballot_id`**, writes a `ballot` row containing `subject_id` and `round_id` only, sets `invite.redeemed = true`, and 302s to `/b/{ballot_token}`.
3. **No column anywhere links `invite` to `ballot`.** The rater's browser holds the only continuation handle, which is what makes save-and-resume work without a login.
4. `redeemed` is a boolean, not a timestamp — a `redeemed_at` would allow re-identification by correlating it with `ballot.created_at`. This is a real attack and worth calling out in the demo.
5. `relationship_to_subject` stays in `identity.db` and is **never** copied to the ballot. That is what "no breakdown by role" means at the schema level in round 1.

**Accepted trade-off (document it):** losing the link means losing the resume link. Mitigation is re-issue by admin, which mints a *new* ballot and voids the old one. Emphasize this in the ethics note — it is a deliberate confidentiality-over-convenience choice.

### Conversational engine (LangGraph)

State machine, one graph, `SqliteSaver` checkpointed on `thread_id = ballot_id` — save-and-resume falls out of the checkpointer for free.

```
welcome → consent ─(decline)→ exit_gracefully
   │(accept)
   ▼
likert_block(C1..C6)              ← selectable response, deterministic, no LLM
   │
   ▼
open_probe(per competency)        ← LLM generates the probe from the question bank
   │
   ├─▶ specificity_judge ──(vague / <N tokens)──▶ follow_up  (max 2, budget-capped)
   │                       └─(specific)─────────┐
   ▼                                            │
qualitative_block (strengths, opportunities) ◀──┘
   │
   ▼
safety_gate ─(crisis signal)→ signpost_EAP + halt scoring
   │
   ▼
pii_redactor  →  review_and_confirm  →  submit  →  freeze
```

Design rules:
- **Routing is deterministic Python; the LLM only generates question text and returns structured judgments** (Pydantic `SpecificityVerdict`, `SafetyVerdict`). This is what makes the flow testable and the wording auditable.
- Follow-up budget: max 2 per open item, max ~8 per session. Prevents the "endless interview" abandonment failure mode.
- Every generated probe is traceable to a `question_bank` row; nothing is invented free-form.
- Guardrails: no therapy, no HR/legal advice, no speculation about the subject's private life, refuse to compare named individuals. Crisis signposting to `[EAP link]` from `consent.md`, and the session can still be saved.
- PII redaction runs **when a report is rendered for the subject** (revised — see §3.1; it originally ran before persistence). Names, emails, ticket IDs, and self-identifying constructions ("as their only direct report…") are stripped or generalized from that copy. HR reads the original.

### Aggregation and redaction

- Roll up: item → competency → dimension (Delivery 60% / Soft Skills & Values 40%) → cohort. The overall is the weighted combination of the two, not a pool of items — see §1.1.
- **Small-N suppression:** default `k = 4` ballots per cell (`reporting-rules.yaml`). Below `k`, the cell renders `—` with a suppression reason code. Also suppress on **dominance** (one ballot >50% of a cell's weight) and apply **complementary suppression** so a suppressed cell can't be recovered by subtracting from the total. That last one is where most implementations get it wrong and is worth a test.
- Qualitative: cluster open answers into themes via structured-output LLM, require `min_ballots_per_theme = 3`, and emit **paraphrased** themes. Verbatim quotes are re-identifying — no raw quotes leave the system in round 1.
- Exports: **CSV** for structured aggregates, **PDF** for the two narrative reports.

### Security posture (prototype, honest about mocks)

| Control | Prototype | Production note |
|---|---|---|
| Rater auth | Tokenized magic link, 256-bit, hashed at rest, single redemption, expires with the window | Same |
| HR/Admin auth | Mock login, session cookie, role in session | OIDC/SSO |
| RBAC | Three roles — Rater, Manager/Leader, HR/Admin — enforced in FastAPI dependencies | Same |
| Encryption in transit | TLS terminated at proxy; demo runs localhost, documented as mock | Same |
| Encryption at rest | Field-level Fernet on free-text answers + transcripts, key from env | Managed Postgres + KMS |
| Audit | Append-only `audit_log(actor_id, role, action, resource, ts)` on **every** aggregate/report/export read | Ship to SIEM |
| Retention | `purge_raw` job, 12 months on raw responses and transcripts; aggregates retained indefinitely | Same |

---

## 4. Repository layout

```
conversational-360/
├── PLAN.md
├── README.md                       # setup, run, demo
├── requirements.txt
├── .env.example
├── docs/
│   ├── architecture.md  +  architecture.png      # components, data flow, trust boundaries
│   ├── erd.md           +  erd.png               # incl. identity separation
│   ├── ethics-privacy-note.md                    # threats + mitigations
│   ├── prompt-design.md                          # instrument + branching logic
│   └── demo-script.md                            # 10–15 min
├── inputs/                         # the "minimum inputs provided"
│   ├── competencies.json
│   ├── questions.json
│   ├── consent.md                  # EN + ES
│   ├── reporting-rules.yaml
│   └── raters-synthetic.csv
├── app/
│   ├── main.py
│   ├── config.py
│   ├── db/            models_identity.py · models_responses.py · session.py · seed.py
│   ├── routers/       rater.py · admin.py · reports.py · auth.py
│   ├── graph/         state.py · nodes.py · guardrails.py · redaction.py · build.py
│   ├── aggregation/   rollup.py · suppression.py · themes.py
│   ├── reporting/     individual.py · cohort.py · pdf.py · csv_export.py
│   ├── security/      tokens.py · rbac.py · crypto.py · audit.py
│   └── templates/     consent · chat · admin · report_individual · report_cohort
└── tests/
    ├── test_suppression.py         # REQUIRED by the brief
    ├── test_identity_separation.py
    ├── test_graph_flow.py
    ├── test_guardrails.py
    └── test_retention.py
```

---

## 5. Four-week schedule

MVP cut line is marked. Everything below it ships only if the line above is green.

### Week 1 — 2026-08-19 → 08-25 · Foundations and the instrument
- [ ] Scaffold repo, FastAPI app, SQLAlchemy models, two-database split, Alembic-less simple `create_all` seeding.
- [ ] Author **`competencies.json`** — port all 6 core competencies with their 3 sub-items and both anchor-text variants verbatim from the client doc.
- [x] ~~Draft **B1–B4** behavioral competencies~~ — withdrawn, see §1.1: the bucket they were meant to fill is fully specified as Sección II A.
- [ ] **`questions.json`** — Likert items + open-probe templates + `allow_followup` flags. The brief's starter `taxonomy.csv` is absorbed here rather than kept as a separate file: its `dimension_tag` column is the competency list and its `example_prompt_theme` column becomes `open_probes`, which carries several framings per competency instead of one. Keeping both would mean two places to edit a competency name with nothing detecting the drift.
- [ ] **`consent.md`** (EN, with ES stub), **`reporting-rules.yaml`** (k=4, thresholds, dimensions), **`raters-synthetic.csv`** (~40 raters × 6 subjects × 2 cohorts — enough rows that small-N suppression actually triggers in some cells and not others).
- [ ] Token issuance + redemption + ballot minting, with `test_identity_separation.py` asserting no query path can join a ballot back to a rater.
- **Exit:** a magic link opens a page that shows the consent copy and creates an unlinked ballot.

### Week 2 — 2026-08-26 → 09-01 · The conversation
- [ ] LangGraph state + graph build with `SqliteSaver` keyed on `ballot_id`.
- [ ] Likert block (deterministic) and open-probe block (LLM-generated from bank).
- [ ] `SpecificityVerdict` structured-output judge + capped follow-up loop.
- [ ] Guardrails: crisis detection → EAP signpost, therapy/HR-advice refusals, scope refusals.
- [ ] PII redactor before persistence.
- [ ] Mobile-web rater UI: consent → chat → review → submit, HTMX partials, thumb-friendly Likert buttons, visible progress and time estimate.
- [ ] Save-and-resume verified by closing the tab mid-session and reopening the ballot link.
- **Exit:** a synthetic rater completes a full 360 on mobile Safari and the record persists redacted.

### Week 3 — 2026-09-02 → 09-08 · Aggregation, reports, admin
- [ ] Roll-up by item → competency → dimension → cohort.
- [ ] Suppression: threshold-k, dominance, complementary. `test_suppression.py` first — write the tests before the implementation here.
- [ ] Theme clustering with `min_ballots_per_theme`, paraphrase-only output.
- [ ] **Individual Manager-Ready Report**: score bands, gaps (self vs. aggregate is out of scope in round 1 — use competency spread instead), insights, *suggested conversation openers*, and a **next steps / commitments** section.
- [ ] **Team/Cohort Report**: seniority distribution across bands, competency heat map, trends placeholder (single round = no trend yet; render the structure with an "insufficient history" state).
- [ ] CSV export + PDF rendering.
- [ ] Admin console: manage competency and question banks, open/close the window, **freeze dataset** (immutable snapshot + content hash).
- [ ] `audit_log` on every report/export read; RBAC dependencies on all three routers.
- **── MVP CUT LINE ──** Everything above must be working end to end by 09-08.

### Week 4 — 2026-09-09 → 09-15 · Harden, document, rehearse
- [ ] Architecture diagram with trust boundaries; ERD with the identity split drawn explicitly.
- [ ] `prompt-design.md` — full instrument and branching logic.
- [ ] `ethics-privacy-note.md` — threat table (re-identification via free text, via timing, via small cohorts, via admin curiosity, via LLM provider retention) with the mitigation for each.
- [ ] Retention job + `test_retention.py`.
- [ ] Field-level encryption on free text.
- [ ] `demo-script.md` and a timed rehearsal (see §7).
- [ ] README, `.env.example`, clean install from scratch on a fresh venv.
- **Stretch, in priority order:** (1) ES/EN toggle — highest real-world value for Pluralit; (2) richer manager themes; (3) HRIS read-only stub.

**Buffer:** 09-16 is deliberately unassigned.

---

## 6. Deliverables checklist

| # | Deliverable | Where | Week |
|---|---|---|---|
| 1 | Architecture diagram (components, data flow, trust boundaries) | `docs/architecture.*` | 4 |
| 2 | Working prototype (web) | `app/` | 2–3 |
| 3 | Data schema / ERD with identity separation | `docs/erd.*` | 1 → 4 |
| 4 | Questionnaire + prompt design + branching logic | `docs/prompt-design.md`, `inputs/` | 1–2 |
| 5 | Aggregation + redaction with small-N tests | `app/aggregation/`, `tests/test_suppression.py` | 3 |
| 6 | Ethics & privacy note | `docs/ethics-privacy-note.md` | 4 |
| 7 | Demo script (10–15 min) | `docs/demo-script.md` | 4 |
| 8 | Individual Manager-Ready Report | `app/reporting/individual.py` | 3 |
| 9 | Team/Cohort Report | `app/reporting/cohort.py` | 3 |

---

## 7. Demo script skeleton (10–15 min)

1. **0:00** — The problem, in the client's own terms: a Word table, 18 items, filled by one manager, feeding a promotion decision. (1 min)
2. **1:00** — Admin opens a round, generates tokenized invites, shows that no rater identity ever reaches the response store. (2 min)
3. **3:00** — Rater journey on a phone: consent, time estimate, Likert block, one adaptive follow-up that visibly digs deeper, then close the tab and resume. (4 min)
4. **7:00** — Trigger a guardrail live: a crisis phrase → EAP signpost; a "should I fire them?" → refusal. (1.5 min)
5. **8:30** — Close and freeze the window; show the snapshot hash. (1 min)
6. **9:30** — Individual report, then the cohort report with a **visibly suppressed** small-N cell, and explain complementary suppression. (3 min)
7. **12:30** — Audit log showing who read what. Close on the boundary: no automated verdicts. (1.5 min)

---

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Re-identification of a reviewer **by the subject** through free text | Highest — kills the confidentiality promise that remains after §3.1 | Redact when rendering the subject's copy, paraphrase-only themes, min-N per theme, no verbatim quotes |
| Raw text at rest now that masking moved out of the write path | Medium — a reporting bug could expose what a write-time scrub used to make impossible | `AUDIENCE_SUBJECT` is the default so callers fail closed; subject-copy masking is asserted in the suite; 12-month raw retention now does real work |
| Consent copy drifting from what the code discloses | High — it is a promise to a person, not a doc comment | `test_consent_copy_discloses_that_hr_can_attribute` fails the build if the disclosure is removed |
| Synthetic roster too small for suppression to be visible | Weak demo, weak evaluation score | Design the roster in Week 1 so some cells pass k and some fail; it is the money shot of the demo |
| Conversational flow feels endless → abandonment | Directly hits an evaluation criterion | Hard follow-up budget, progress indicator, honest time estimate, resume always available |
| Scope creep into automated seniority scoring | Violates a stated business rule | §2 boundary; no code path outputs a level or recommendation |
| PDF toolchain friction on Intel macOS | Week 3 slip | Primary WeasyPrint; if system deps fight back, fall back to `xhtml2pdf` — same HTML templates either way |
| LLM latency in a live demo | Awkward demo | Pre-warm, cache the demo ballot, and have a recorded fallback clip |
| Solo bandwidth over 4 weeks | Slip past the cut line | Cut line at 09-08; stretch goals are genuinely optional |

---

## 9. Open questions for the client / HR

1. Round 1 is fully anonymous with no role breakdown. Round 2 presumably wants self-vs-manager gap analysis — should the schema leave a *forward-compatible* seam for that, or stay hard-split?
2. Threshold: 2.5 (capstone) or 2.6 on Autonomy + Innovation (client doc)? Config default is 2.5.
3. **Answered by §1.1** — B1–B4 were withdrawn, so there is no drafted wording left needing sign-off. Every item is now the client's own. One item does need HR's ruling: C5.2, where the document contradicts itself (*Entusiasmo y clima laboral* in the narrative, *Orientación a resultados* in the template). We follow the template.
4. Real EAP / well-being contact link for `consent.md`.
5. Is the round scoped to the leadership cohort only, or the full cross-functional list (Talent Partner, Sourcer, Legal & Immigration, Business Process, Comms, Admin & Finance)? Cohort sizing drives whether `k = 4` is even reachable.

   **Answered, and it forced a change.** The client's floor is three evaluations per subject *including the self-assessment* — two anonymous raters. At `k = 4` every per-subject figure suppresses and the individual report is entirely blank. `min_n` is now `2`, which matches the client's reality but cannot deliver the brief's "fully anonymous" requirement by inference, only structurally. `consent.md` was rewritten to promise *confidential* rather than *anonymous*. Raising it back to 3 is a client decision about invite counts, not a config change. See README "Round size, and an honest limit".

7. The self-assessment is **excluded from the average** and shown only as a comparison point ("poniendo foco en la autoevaluación... como punto de comparación"). Built: it lives in its own table outside the ballot pool, so it can never reach an aggregate and never needs a role marker on an anonymous ballot. Confirm with HR that the manager seeing the subject's self-ratings is intended.
6. **Answered at demo 2, and it inverted a core assumption.** HR needs to see which reviewer evaluated which subject and what they wrote; masking is for the subject's copy only. Implemented as §3.1 describes. Two things follow that are **not** engineering decisions and need a human answer:
   - **The brief forbids this.** *"Do not present individual attributable feedback to anyone"* is a stated requirement and an evaluation criterion ("alignment with HR/confidentiality intent"). Confirm the client's instruction takes precedence over the brief for grading, or carry both by keeping attribution switchable per round.
   - **Reviewers who already responded** did so under a consent screen promising that nobody, including HR, could look up what they said. The POC's data is synthetic so nothing real is affected, but in a live round that data cannot be re-scoped retroactively — it would need re-consent or exclusion.
7. Are external collaborators (advisors, key partners) in round 1? They need a distinct consent scope.
