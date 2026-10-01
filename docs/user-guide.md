# Conversational 360° — user guide

Conversational 360° collects 360° feedback as a short conversation instead of a
form. Reviewers rate a set of competencies, explain their ratings in their own
words, and the app asks for more detail where an answer is too vague to be
useful. HR then reads the results as reports: one copy for HR, and one with
reviewers' identities hidden for the person being reviewed.

This guide is for the two kinds of user:

- **HR admins**, who decide who is reviewed and by whom, send the links and read
  the reports — [For HR admins](#for-hr-admins).
- **Reviewers**, who receive a link and give feedback — [For reviewers](#for-reviewers).

> **This is a demonstration.** Every person, rating and comment in it is
> invented, and the banner at the top of each page says so. The names look real
> on purpose, so we can check that names are hidden properly. What you enter is
> kept, and everyone using the demo sees the same data — please use test names
> and test email addresses only, never a real colleague's.

---

## Signing in

| | |
|---|---|
| **Address** | https://conversational-360.onrender.com |
| **HR admin account** | Username and password sent to you separately |
| **Reviewer account** | Sent by HR together with each review link |

Sign in with the account for what you are doing. HR admins land on the round
dashboard; reviewers land on a short welcome page. If you open a review link
before signing in, you are asked to sign in first and then taken straight to that
review.

At the top right of every page:

- **Signed in as …** shows which account you are using, with **Sign out** next
  to it.
- **☾ Dark / ☀ Light** switches the colour theme. Your choice is remembered in
  this browser.

---

## For HR admins

### The pages you'll use

| Page | How to get there | What it's for |
|---|---|---|
| **Dashboard** | **Dashboard** at the top, or the site name | Everyone in the round in organisation order, how many reviews each has, adding people, reports and round settings |
| **Person page** | Select a name on the dashboard or on Ballot links | Everything about one person: who might review them, their review links and their self-assessment link |
| **Ballot links** | **Ballot links** at the top | Every review link in the round, its status, and the self-assessment links |
| **Reports** | **Reports** on the dashboard | Individual reports (HR copy and subject copy), department reports, and a spreadsheet export |

### Running a review, step by step

#### 1. Check who is in the round

The dashboard lists everyone in **organisation order**: the person at the top
first, then each manager followed by their team, indented one level deeper. Under
each name you see their role, their department and who they report to.

To add someone, use **Add a person to review**:

| Field | Notes |
|---|---|
| **Name** | Required. Appears on every report about them. |
| **Role** | Their job title, for example *Senior Developer*. |
| **Department** | Pick an existing one or type a new one. Department reports group people by this. |
| **Reports to** | Choose from people already on the list, or leave it for the top of the organisation. This is what places them in the organisation and suggests their reviewers. |
| **Email** | Optional. Pre-fills their address when they are suggested as someone's reviewer. |

The person appears straight away, with no reviews yet. Their name is also added
to the list of names hidden from subject copies from that moment on.

#### 2. Open the person to be reviewed

Select their name. Their **person page** shows their role, department, manager
and email, then everything you need to collect their feedback.

#### 3. Mint a link for each reviewer

A **link** is a personal address for one review of this person. Each reviewer
needs their own.

**Suggested reviewers** lists the people who work closest to them, taken from
the reporting lines:

- **Manager** — who they report to,
- **Direct report** — who reports to them,
- **Peer** — who shares their manager.

For each one you want to involve, check the address (it is pre-filled where we
have one; change it or leave it empty if you prefer) and select **Mint link**.
The row then shows **Link sent to … · status**, and the new link appears
highlighted under **Links for reviewing …**.

**Someone else** mints a link for any other reviewer — a client, a partner, a
colleague from another team. Any address works, inside or outside the
organisation.

> **The address you note is what puts a name on the answers** in the HR copy of
> the report. A link with no address shows as an "unassigned link". You can add
> or correct an address later on **Ballot links**, even after the link has been
> sent.

The relationship (manager, peer, direct report) is only used to make
suggestions. It is not saved with the link, and no report ever breaks feedback
down by it.

#### 4. Send the links yourself

Select **Copy link** next to a link and send it to the reviewer by email, chat
or however you normally would, together with the reviewer account details. For
example:

> Hi Marcos, we're collecting 360° feedback on Ana Torres and would value yours.
> It's a short conversation and you can stop and come back to it at any time.
> Link: *(paste)* — sign in with username *…* and password *…*.

The app does not send emails itself.

#### 5. Send the self-assessment

On the person page, **Self-assessment** shows their personal self-assessment
link with its status. If there isn't one yet, select **Mint self-assessment for
…**, then copy it and send it to the person being reviewed. Minting again gives
the same link, so one you have already sent keeps working.

The self-assessment is never counted in any score. It appears in the report
next to the reviewers' view, as a starting point for the development
conversation.

#### 6. Track progress

**Ballot links** shows every link in the round, with a summary at the top:

| Status | Means |
|---|---|
| **not opened** | Nobody has started this review yet — **or** the reviewer opened it and declined. These look the same on purpose: reviewers are promised that declining leaves no trace. |
| **in progress** | Consent given, unfinished. The figure next to it is how many ratings they have given out of the total. |
| **submitted** | Finished and counted. |
| **frozen** | Submitted, and the round has since been frozen. |

Links with an address that are still outstanding are counted at the top, so you
know whom to remind. A reviewer who lost their link can simply be sent the same
one again: it reopens their review where they left off.

On the dashboard, **Rater responses** shows each person's submitted reviews
against the minimum needed before their scores are shown. It stays red until
the minimum is reached.

#### 7. Read the reports

Under **Reports** on the dashboard:

- **Individual** reports open as the **HR copy**. The buttons at the top switch
  to the **Subject copy**, which is what the person being reviewed and their
  manager would receive. Comparing the two shows exactly what is hidden.
- **Department** reports summarise everyone in a department, which is useful
  context for people with too few reviews of their own.
- **Download aggregates.csv** gives all the figures as a spreadsheet.

Some figures show a dash instead of a number. That is intentional: a figure is
only shown once enough reviews contribute to it, so that no single reviewer can
be identified. The report says why each one is hidden.

#### 8. Freeze the round

**Round controls → Freeze round** closes the round and records a fingerprint of
the results, so you can later show they were not changed. Reviews submitted
afterwards still count; select **Re-freeze** to bring the fingerprint up to
date. This affects everyone using the demo, so agree it with other users first.

### Trying a review yourself

You can open any review or self-assessment link while signed in as HR admin.
A banner reminds you that what you submit counts as a real review, or — on a
self-assessment — that saving changes the person's answers. To see exactly what
a reviewer sees, sign out and sign in with the reviewer account.

### How written questions are chosen

Every reviewer rates every competency. Only some competencies then lead to a
written question, chosen from how they were rated: very low, very high, mixed,
or partly not observed. A thin answer can get a follow-up asking for an example
or the effect it had.

**How written questions are chosen** on the dashboard lets you balance depth of
feedback against interview length:

- how many competencies get a written question,
- how many follow-ups one question can get, and how many per interview,
- the shortest answer accepted without a follow-up.

Each setting shows its allowed range and its original value; **Restore shipped
defaults** undoes your changes. Changes apply from the next question, for
everyone. The wording of the questions is not editable here, because it has been
reviewed by HR and Legal.

### The AI model

The conversation is run by an AI model, which words the follow-up questions to
fit what the reviewer just said and judges whether an answer is specific enough.
The order of questions, which competencies get one, the limits on follow-ups and
the safety checks are fixed rules that do not depend on the model.

**Model activity** on the dashboard lists the most recent model calls. If a
banner says **"The AI model is not available"**, the model is not configured for
this site: the app still works, but questions use fixed wording and answers are
not judged by the model. Contact your administrator.

---

## For reviewers

### Getting started

HR sends you a **personal link** for each review you're asked to give. Open it,
sign in with the reviewer account details HR gave you, and you go straight to
that review. The top of the page always shows whom you are reviewing.

**Your progress saves as you go.** You can close the page at any time — opening
the same link again brings you back to where you stopped. Keep the link (bookmark
it, or keep HR's message) until you have submitted.

### What the review looks like

1. **Consent.** You read who will see your answers and choose **I accept** or
   **I decline**. Declining closes the review and records nothing.
2. **Ratings.** You rate the person on each competency. **What do these levels
   mean?** describes each level. Choose **Not observed** only for something you
   have genuinely not seen — it is left out of their scores rather than counted
   against them.
3. **Written questions.** For some competencies you're asked to say more, for
   example a situation that shows what you rated.
4. **Follow-ups.** If an answer is very short or general, you may be asked for a
   concrete example or what changed as a result.
5. **Closing questions.** A few open questions about strengths and what the
   person could do next.
6. **Review and submit.** You see all your ratings and written answers together
   and can change any of them. Changing a rating can occasionally bring up one
   new question. Then select **Submit**, or **Not yet** to come back later.

**You can change any answer at the end**, before you submit — so don't worry
about getting something exactly right the first time. Once submitted, a review
can't be changed.

### Who sees your answers

- **HR** sees your answers as you wrote them, together with the address HR sent
  your link to, to run the review round.
- **The person you are reviewing, and their manager,** get a version with names
  and contact details removed, written answers summarised as themes rather than
  quoted, and nothing that says who wrote what.
- No report shows feedback broken down by whether it came from a manager, a peer
  or a direct report.

### Things the interview won't do

To keep the feedback fair and useful, the interview declines and asks you to
rephrase if an answer asks it to decide promotion, pay or an overall score;
asks for medical or mental-health advice; brings up someone's private life; or
compares the person with another named colleague.

If something you write suggests you may be going through a hard time, the
interview pauses and points you to support. Your progress is kept.

---

## The self-assessment

If you are the person being reviewed, HR sends you a self-assessment link. It is
a plain form, not a conversation: one page per competency, rated with the same
levels the reviewers use.

- **Save and continue** saves each page as you go; **← Back** and the page list
  at the top let you move around without losing anything.
- You can come back and change any answer until the round is closed.
- Your self-assessment is **never** counted in any average. It appears in your
  report next to how others see you, as a starting point for the conversation.

---

## Who sees what

| | HR admin | The person reviewed, and their manager | Reviewers |
|---|---|---|---|
| Ratings and scores | ✅ | ✅ (where enough reviews contribute) | — |
| Written answers | ✅ as written, with the address the link was sent to | Summarised as themes, names and contact details removed | Only their own |
| Who wrote what | ✅ | ❌ | — |
| Breakdown by manager / peer / direct report | ❌ | ❌ | — |
| Self-assessment | ✅ | ✅ as a comparison, never in averages | — |

---

## If something goes wrong

| What you see | What to do |
|---|---|
| **"This link isn't valid"** | Check you have the whole link from HR's message. If it still fails, ask HR to send it again. |
| **A sign-in page** when opening a link | Sign in with the account HR gave you; you are then taken to the review. |
| **"The AI model is not available"** banner | The app still works with fixed question wording. Let your administrator know. |
| **"This page is for HR admins only"** | You are signed in with the reviewer account. Sign out and sign in as HR admin. |
| **A reviewer lost their link** | Send them the same link again from **Ballot links** or the person page — it resumes their review. |

When reporting a problem, please note the page, what you did and what you
expected to happen. A screenshot helps.
