# Deploying the demo to Render

**This repository is the working copy of the project**, and it is what Render
deploys. The client's repository on `gitlab.kdsoft.io` receives a copy for
delivery — see the last section. Render could not deploy from there in any case:
it only integrates with GitHub, GitLab.com and Bitbucket.

The project sits at the repository root, so no Root Directory setting is needed.

---

## 1. Create a Web Service

Render → **New** → **Web Service** → connect `pauburgos10/conversational-360`.

**Web Service** is the right type: it gets a public HTTPS URL and runs a
long-running server. *Static Site* has no server process, and *Private Service*
has no public URL — only other Render services can reach it, so HR could not.

Fill in the form:

| Field | Value |
|---|---|
| Branch | `main` |
| Root Directory | *leave blank* |
| Language / Runtime | **Docker** — Render should detect the `Dockerfile` |
| Instance type | **Starter** (paid) — see §1a for why free loses data |
| Health Check Path | `/healthz` — may be under *Advanced* |

Leave any build or start command empty: the `Dockerfile` defines both, and a
start command typed here would override its `CMD`.

### Environment variables

Ten, entered by hand. Add the secrets as **secret** values so they are not
readable from the service page afterwards.

| Variable | Value | Why |
|---|---|---|
| `C360_SEED_ON_BOOT` | `1` | Seeds the synthetic round when the store is empty. Free tiers have ephemeral disks, so without this a cold start shows "not enough responses" everywhere |
| `C360_DEMO_BANNER` | `1` | Puts "every person here is synthetic" on every page. The seeded subjects have plausible human names, deliberately |
| `C360_ROSTER_IN_DATA` | `1` | **Required with the disk.** Keeps the subject list on the disk beside the database — see §1a |
| `OPENAI_MODEL` | `gpt-4o-mini` | ~28 calls per completed interview; a fraction of a cent at this model |
| `OPENAI_API_KEY` | **secret** | From a **dedicated** OpenAI project with a monthly budget cap — never your personal key. See README, "Letting HR test against a real model" |
| `C360_RATER_USER` | **secret** | Username of the rater account — the one reviewers sign in with |
| `C360_RATER_PASSWORD` | **secret** | Its password |
| `C360_HR_USER` | **secret** | Username of the HR admin account |
| `C360_HR_PASSWORD` | **secret** | Its password. Each account needs both halves; with no account set, nobody can sign in |

**Do not set `C360_LLM_MODE`.** The app runs live whenever `OPENAI_API_KEY` is set
and shows a "model not available" banner when it is not. There is no switch in
the app any more, so `C360_LLM_MODE=stub` here would keep the model off for every
visitor. If an older setup has it, delete it.

Create the service, and Render builds from the `Dockerfile`.

## 1a. Keep data across restarts: a paid instance and a disk

**Why.** On the free plan the service sleeps after ~15 minutes idle and wakes in a
fresh container with an empty filesystem. Everything under `/app/data` is lost —
minted links, assigned addresses, interviews, subjects added through `/admin` —
and the round is reseeded, so the page looks intact while HR's work is gone.

**Steps** (Render's labels and prices change; check the dashboard as you go):

1. On the service, open **Settings → Instance Type** and choose **Starter**. Render
   needs a payment method on the workspace first. Paid instances also do not
   sleep, so the 30–50 second wake-up disappears too.
2. Open **Disks** → **Add Disk**:

   | Field | Value |
   |---|---|
   | Name | `c360-data` |
   | **Mount Path** | **`/app/data`** — exactly this; the app writes nowhere else |
   | Size | 1 GB is ample |

3. Add `C360_ROSTER_IN_DATA=1` to the environment (it is in the table above).
4. Save. Render redeploys with the disk attached.

**Why `C360_ROSTER_IN_DATA` is not optional.** Every file the app writes lives in
`/app/data` except one: the subject list, `inputs/subjects.csv`, which `/admin`
appends to. Without the variable, a restart restores the bundled list while the
disk keeps the database — so a subject added through `/admin` keeps their ballots
but drops out of the list the redactor masks. **Their name then appears unmasked
in the subject's copy of every report about them.** With the variable, the live
list is copied onto the disk on first boot and never overwritten after, so the
two persist together. `tests/test_persistent_roster.py` reproduces the leak and
the fix.

**Two consequences to know about**

* **Deploys briefly interrupt the service.** A disk attaches to one instance at a
  time, so Render stops the old instance before starting the new one instead of
  switching over with no downtime. Expect a short gap on every push.
* **Once the disk holds a subject list, it wins.** Editing `inputs/subjects.csv`
  and redeploying will not change the live list. Add subjects through `/admin`,
  or delete `/app/data/subjects.csv` from the Render shell to re-copy the bundle.

**Probe-policy edits persist too.** Settings HR changes on the dashboard are stored
in `/app/data/probe_policy.json`, so they live on the disk with everything else —
and, on the free plan, are wiped with everything else.

**The round is still seeded once.** `C360_SEED_ON_BOOT` only seeds an empty
database, so the first boot on a new disk creates the synthetic round and every
later boot leaves it — and everything HR added — alone.

### About `render.yaml`

A manually created Web Service **does not read `render.yaml`**. The file stays as
the written record of what the service needs, and doubles as a Blueprint if you
ever deploy through Render's Blueprint flow instead. Nothing keeps the two in
step — if you change a variable in the dashboard, change it in the file too.

## 2. Check it came up

1. `https://<service>.onrender.com/healthz` → `{"ok": true}`, no sign-in needed.
   It sits outside sign-in because a probe that gets redirected reads as a failed deploy.
2. `https://<service>.onrender.com/` → the sign-in page, with the yellow
   demonstration banner.
3. Sign in as HR → the round dashboard lists the subjects and reports render.
4. On **Ballot links**, mint a link, sign out, sign in as the rater and open it.
   There should be **no** "AI model is not available" banner; if there is, the
   API key is missing (or `C360_LLM_MODE` is still set).

If the round is empty, `C360_SEED_ON_BOOT` did not take. If the sign-in page says
no accounts are configured, one half of each credential pair is missing.

## 3. Send it to HR

The URL and the HR admin login — nothing else. No install, and it works on a phone.
HR hands the rater login to reviewers along with each ballot link they send.

Tell them two things:

* **What they enter is kept** on the paid instance with a disk — so they should
  use test names and addresses only, never a real colleague's. (On the free plan
  the opposite holds: everything is wiped when the service sleeps.)
* **Everyone in it is invented.** The banner says so, but say it as well.

## Controlling what HR sees

Render redeploys on every push to `main`. Because this is also the repository you
develop in, **pushing unfinished work to `main` puts it in front of HR.** Two
ways to keep control:

* **Work on a branch and merge to `main` when it is ready.** The merge is the only
  moment HR's demo changes.
* **Turn off auto-deploy** on the service, and deploy chosen commits by hand.

## Delivering to the client repository

The client's repo (`gitlab.kdsoft.io`, project under `paula/conversational-360/`)
is updated **from** this one, periodically. It is currently behind: the
probe-policy panel, the persistent roster, and `.dockerignore` exist only here.

Copy with a tool that keeps hidden files. A Finder drag or `cp -R src/* .`
**silently skips dotfiles** — that is how `.gitignore` went missing when this repo
was created. Dry-run first: `--delete` removes files on the receiving side, and
that side is a shared repository.

```bash
CLIENT="<path to client repo>/paula/conversational-360"
rsync -a --delete --dry-run --itemize-changes \
  --exclude .git --exclude .venv360 --exclude .env --exclude data \
  --exclude '*.docx' \
  ./ "$CLIENT/"                     # read the list, then run again without --dry-run
```

Four things to settle in the client repo when you do this:

1. **Its `.gitignore` is weaker than this one** and has already let generated
   files through: commit `a7e3fce` (pushed to `origin/demo-paula`) contains
   `data/invites.json`, `web-settings.json` and `data/reports/`. The rsync above
   brings the stronger `.gitignore` across; the tracked files still need
   `git rm --cached` to stop being committed.
2. **The client compensation document is in that repo's pushed history** (commit
   `9b385ad`). Deleting it from the tree does not remove it from history — raise it
   with whoever administers the repository before rewriting anything.
3. **Deployment files do not fit its layout.** `DEPLOY.md` and `render.yaml`
   describe this repo: root layout, GitHub, `main`. Decide whether they belong in
   the client repo at all, and if so, what they should say there.
4. Check `git status` there before committing: no `.env`, no `data/` files, no
   `.docx`.

## What this deployment is not

Sign-in is two shared accounts in front of a demo, not real authentication: every
HR admin shares one login, and so does every reviewer, so the app cannot tell
people apart or audit who did what. The HR account reads every report, including
verbatim feedback attributed by name. That is fine for a synthetic round and
disqualifying for a real one.

Running a real pilot needs proper authentication, a hosting arrangement the client
accepts under GDPR, and the two open decisions in PLAN.md §9.6. None of those is a
packaging problem.
