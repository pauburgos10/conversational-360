# Deploying the demo to Render

This repository exists to feed Render. **The canonical project lives in the
client's GitLab** (`gitlab.kdsoft.io`), which Render cannot connect to — it only
integrates with GitHub, GitLab.com and Bitbucket. Changes are made there and
copied here to deploy. Treat this repo as a build artifact: it can be deleted and
recreated from the canonical source at any time.

The project sits at the repository root, so `render.yaml` is picked up as a
Blueprint and no Root Directory setting is needed.

---

## 1. Create the service from the Blueprint

Render → **New** → **Blueprint** → connect `pauburgos10/conversational-360`.

Render reads `render.yaml` and proposes one web service, `conversational-360-demo`
(Docker, free plan, health check `/healthz`). It then prompts for the three
secrets the file leaves blank:

| Secret | What to enter |
|---|---|
| `OPENAI_API_KEY` | A key from a **dedicated** OpenAI project with a monthly budget cap — never your personal key. See README, "Letting HR test against a real model" |
| `C360_DEMO_USER` | HTTP Basic username you will give HR |
| `C360_DEMO_PASSWORD` | HTTP Basic password. Both this and the user must be set, or the gate stays open |

The other four variables come from the file and need no input:

| Variable | Value | Why |
|---|---|---|
| `C360_LLM_MODE` | `stub` | Boot offline. HR opts into live calls from `/admin`; a restart returns here, so an idle demo never spends money |
| `C360_SEED_ON_BOOT` | `1` | Seeds the synthetic round when the store is empty. Free tiers have ephemeral disks, so without this a cold start shows "not enough responses" everywhere |
| `C360_DEMO_BANNER` | `1` | Puts "every person here is synthetic" on every page. The seeded subjects have plausible human names, deliberately |
| `OPENAI_MODEL` | `gpt-4o-mini` | ~28 calls per completed interview; a fraction of a cent at this model |

Apply, and Render builds from the `Dockerfile`.

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

* **Anything they type is wiped when the demo restarts.** The free tier sleeps
  after ~15 minutes idle and comes back with a fresh disk, so the first click
  after a pause takes 30–50 seconds and the round resets to its seeded state.
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
