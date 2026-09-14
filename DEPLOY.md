# Deploying the demo to Render

This repository exists to feed Render. **The canonical project lives in the
client's GitLab** (`gitlab.kdsoft.io`), which Render cannot connect to — it only
integrates with GitHub, GitLab.com and Bitbucket. Changes are made there and
copied here to deploy. Treat this repo as a build artifact: it can be deleted and
recreated from the canonical source at any time.

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

Eight, entered by hand. Add the three secrets as **secret** values so they are not
readable from the service page afterwards.

| Variable | Value | Why |
|---|---|---|
| `C360_LLM_MODE` | `stub` | Boot offline. HR opts into live calls from `/admin`; a restart returns here, so an idle demo never spends money |
| `C360_SEED_ON_BOOT` | `1` | Seeds the synthetic round when the store is empty. Free tiers have ephemeral disks, so without this a cold start shows "not enough responses" everywhere |
| `C360_DEMO_BANNER` | `1` | Puts "every person here is synthetic" on every page. The seeded subjects have plausible human names, deliberately |
| `C360_ROSTER_IN_DATA` | `1` | **Required with the disk.** Keeps the subject list on the disk beside the database — see §1a |
| `OPENAI_MODEL` | `gpt-4o-mini` | ~28 calls per completed interview; a fraction of a cent at this model |
| `OPENAI_API_KEY` | **secret** | From a **dedicated** OpenAI project with a monthly budget cap — never your personal key. See README, "Letting HR test against a real model" |
| `C360_DEMO_USER` | **secret** | HTTP Basic username you will give HR |
| `C360_DEMO_PASSWORD` | **secret** | HTTP Basic password. Both this and the user must be set, or the gate stays open |

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

**The round is still seeded once.** `C360_SEED_ON_BOOT` only seeds an empty
database, so the first boot on a new disk creates the synthetic round and every
later boot leaves it — and everything HR added — alone.

### About `render.yaml`

A manually created Web Service **does not read `render.yaml`**. The file stays as
the written record of what the service needs, and doubles as a Blueprint if you
ever deploy through Render's Blueprint flow instead. Nothing keeps the two in
step — if you change a variable in the dashboard, change it in the file too.

## 2. Check it came up

1. `https://<service>.onrender.com/healthz` → `{"ok": true}`, no password prompt.
   It sits outside the gate because a probe that gets a 401 reads as a failed deploy.
2. `https://<service>.onrender.com/` → the browser asks for the password.
3. After signing in: the synthetic round lists ten subjects, and the yellow
   demonstration banner is on the page.
4. Switch the header role to **HR / Admin** → `/admin` → reports render.
5. Under Round controls, click **Live** and start an interview. The header should
   show the model name; if it says *degraded*, the API key is missing or rejected.

If the round is empty, `C360_SEED_ON_BOOT` did not take. If there is no password
prompt, one half of the credential pair is missing.

## 3. Send it to HR

The URL and the password — nothing else. No install, and it works on a phone.

Tell them two things:

* **What they enter is kept** on the paid instance with a disk — so they should
  use test names and addresses only, never a real colleague's. (On the free plan
  the opposite holds: everything is wiped when the service sleeps.)
* **Everyone in it is invented.** The banner says so, but say it as well.

## Controlling what HR sees

Render redeploys on every push to `main`. To change HR's version, copy the
updated project here from the canonical GitLab repo and push; that push is the
only moment their demo moves. To freeze it entirely, turn off auto-deploy on the
service and deploy chosen commits by hand.

## Updating from the canonical repo

Copy with a tool that includes hidden files. A Finder drag or `cp -R src/* .`
**silently skips dotfiles** — that is how `.gitignore` went missing the first time
this repo was created, which would have published `data/invites.json` and its
email addresses on the next commit.

```bash
rsync -a --delete \
  --exclude .git --exclude .venv360 --exclude .env --exclude data \
  --exclude '*.docx' \
  "<canonical>/paula/conversational-360/" ./
```

Then check before committing: `git status` should show no `.env`, no `data/`
files, and no `.docx`.

## What this deployment is not

The password is one shared secret in front of a demo. It is **not**
authentication: the role in the header is still a cookie the visitor sets, so
anyone past the password can switch to HR / Admin and read every report —
including verbatim feedback attributed by name. That is fine for a synthetic round
and disqualifying for a real one.

Running a real pilot needs proper authentication, a hosting arrangement the client
accepts under GDPR, and the two open decisions in PLAN.md §9.6. None of those is a
packaging problem.
