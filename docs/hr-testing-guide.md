# Conversational 360° — guide for HR testers

Thank you for testing the prototype. This guide covers how to get in, what each
page is for, and a few things worth trying. You don't need any technical
knowledge.

## Getting in

| | |
|---|---|
| **Address** | https://conversational-360.onrender.com |
| **Username** | `<username>` |
| **Password** | `<password>` |

Your browser will ask for these once when you open the site.

**Before you start, keep in mind:**

- **Everyone and everything in the demo is made up.** The banner at the top of each
  page says so. The names look real on purpose, so we can check that names get
  hidden properly. None of them is an actual employee.
- **Everything you enter is saved** and is still there next time. Please use test
  names and test email addresses only, never a real colleague's.
- **All testers share one demo.** Subjects you add, links you create and settings
  you change are visible to everyone else testing, and they affect them too.

## Two ways of using the site: Rater and HR / Admin

The drop-down at the top right sets who you are acting as:

- **Rater:** someone giving feedback. You see the list of people to review and
  the interview.
- **HR / Admin:** someone running the review. You also get the **Round
  dashboard**, the reports, the review links and the settings.

The site opens as **Rater**. Switch to **HR / Admin** to see everything else.
You can switch back at any time to see what a reviewer sees.

> The drop-down is a testing convenience, not a login. In a real rollout, what
> each person can see would depend on who they are.

The badge next to the drop-down shows whether the AI model is on (**Live ·
model name**) or off (**Offline**). As HR / Admin you can click it to change
that setting (see below).

## Finding your way around

| Page | How to get there | What it's for |
|---|---|---|
| **Round** (home page) | The site name, top left | Everyone being reviewed, how many reviews each has, and buttons to start a review or a self-assessment |
| **Interview** | **Review** on the home page, or a review link | The conversation a reviewer goes through |
| **Self-assessment** | **Self** on the home page | The person being reviewed rates themselves (a plain form, not a conversation) |
| **Round dashboard** | "Round dashboard, reports and exports →" at the bottom of the home page (HR / Admin only) | Add people, track progress, adjust settings, open reports, see model activity, switch the AI on or off |
| **Review links** | **Ballot links** button on the dashboard | Create review links and track who has finished |
| **Reports** | **Reports** section on the dashboard | Individual reports (HR copy and subject copy) and group reports |

## What a reviewer goes through

1. **Consent.** The reviewer reads who will see their answers and chooses
   **I accept** or **I decline**. Declining records nothing.
2. **Ratings.** Each competency is rated using the buttons shown. Tap **"What do
   these levels mean?"** for a description of each level. **Not observed** is for
   something the reviewer has never seen. It is left out of the scores, so it
   doesn't pull them down.
3. **Written questions.** Some competencies get a written question, picked
   according to how they were rated: very low, very high, or mixed. The dashboard
   explains the rules.
4. **Follow-ups.** If an answer is too short or vague, the interview asks for
   more, such as a concrete example or the effect it had.
5. **Closing questions.** The same short set of open questions for everyone.
6. **Review.** Before sending, the reviewer sees all their ratings and answers
   and can change any of them. Changing a rating can add a new question. Then
   **Submit**, or **Not yet** to come back later.

**The link is the bookmark.** Closing the tab saves progress, and opening the
same link again continues from the same question. There is no login for
reviewers.

## Tips

### Creating review links

In a real round, each reviewer would receive their own link. To try that:

1. Switch to **HR / Admin**, open the **Round dashboard**, then click **Ballot
   links**.
2. Under **Mint links**, choose how many links, who they are **For** (the
   person being reviewed) and, if you like, an address to **Assign to**.
3. Click **Mint**. The new links appear in the table below.
4. Open a link (or copy it and send it to another tester) to go through the
   interview as that reviewer.

Good to know:

- **An address is what puts a name on the answers.** Answers from a link with an
  address show that address in the **HR copy** of the report. A link without one
  shows as "unassigned link". The **Review** button on the home page always
  creates an unassigned link.
- You can **add or change the address later**. Edit it in the table and click
  **Save**, even after the link has been sent.
- The **Status** column shows each link as not opened, in progress, or
  submitted. "Chase" appears next to links that have an address but haven't been
  opened yet.
- "Not opened" also covers someone who opened the link and declined. This is
  deliberate: we promise reviewers that declining leaves no trace.

### Switching the AI model on (Auto recommended)

The demo starts **Offline**. Offline, questions are worded from fixed templates,
so they read the same every time, and nothing is sent to an AI model. To see the
model at work:

1. Switch to **HR / Admin** and open the **Round dashboard**, or click the
   **Offline** badge at the top.
2. Under **Round controls → LLM mode**, click **Auto**.
3. The line underneath should read **"Right now: Auto → Live"**, and the badge
   at the top should change to **Live · model name**.

The new setting applies from the next question onwards, and it applies to
everyone using the demo.

**What changes when the model is on:**

- Written questions and follow-ups are **reworded to fit** the competency and the
  ratings just given, instead of using fixed template wording.
- The model **decides whether an answer is specific enough** or needs a
  follow-up.
- **What stays the same:** the order of questions, which competencies get a
  written question, the limits on follow-ups, and the safety checks. These are
  fixed rules and don't depend on the model.

**A good way to see the difference:**

1. Do a review with the model **Offline** and note the wording of the written
   questions.
2. Switch to **Auto** and do another review of the same person with the same
   ratings.
3. Try a vague answer such as *"She is good"* and see how the follow-up asks for
   something concrete. Then give a real example and see it accepted.

**Checking whether the model actually answered:** as HR / Admin, small grey tags
under each interview question show what produced it. When the model was used, the
tag names the model. **Model activity** on the dashboard lists the recent calls.
If a tag reads **fell back after an error**, the model was tried and failed, and
the template wording was used instead.

> Each question worded by the model costs a small amount. When you have finished
> testing the model, please switch back to **Offline**.

### Reports

- **HR copy and subject copy.** Every individual report has two buttons at the
  top. The **HR copy** shows answers as written, with the reviewer's address
  where the link had one. The **subject copy** is what the person being reviewed
  and their manager would receive: names and contact details hidden, nothing
  linked to a reviewer, and written answers summarised as themes rather than
  quoted. Comparing the two is one of the most useful things you can test.
- **Dashes instead of numbers are intentional.** A score is only shown once
  enough reviews have come in, so no single reviewer can be identified. The home
  page shows each person's count against the minimum (for example `1 / 2`, shown
  in red until it is reached), and the report says why each figure is hidden.
- **The self-assessment is never included in averages.** It is shown next to
  the reviewers' view for comparison.
- **No report breaks feedback down by reviewer role.** This is intentional too.
- **Cohort** reports summarise a group, and **Download aggregates.csv** gives
  the figures as a spreadsheet.

### Adding someone to review

On the dashboard, **Add a subject** takes a name, an optional role and a cohort.
The person appears straight away with no reviews yet, which is a good way to
watch a report fill in as reviews arrive. Their name is also added to the list of
names hidden from subject copies. To test that, mention them by name in an answer
about someone else, then open that person's **subject copy**.

### Adjusting how many written questions are asked

**How written questions are chosen** on the dashboard lets you trade depth of
feedback against interview length:

- how many competencies get a written question,
- how many follow-ups a single question can get, and how many per interview,
- the shortest answer accepted without a follow-up.

Each setting shows its allowed range and its original value. Changes apply from
the next question, for everyone. **Restore shipped defaults** undoes them. The
wording of the questions can't be changed here, because it has been reviewed by
HR and Legal.

### Safety and scope checks

These run the same way whether the model is on or off:

- If a reviewer writes something suggesting they may be in crisis, the interview
  **stops** and points them to support. Their progress is saved.
- Milder signs of stress, such as "burnt out", get a short, supportive message
  pointing to support. Then the interview **continues**.
- The interview declines, and asks the reviewer to rephrase, when an answer:
  - asks it to decide promotion, pay or an overall score,
  - asks for medical or mental-health advice,
  - brings up someone's private life,
  - compares the person with another named colleague.

You're welcome to check that these work, but please don't use real personal
circumstances.

### Freezing a round

**Freeze round** under **Round controls** marks the round as closed and records a
fingerprint of the results. Reviews submitted afterwards still count. Click
**Re-freeze** to update the fingerprint. As this affects everyone, please check
with the other testers first.

## Feedback we're after

- Did the interview feel like a conversation or like a form? How long did it take?
- Were the written questions and follow-ups relevant, especially with the model
  switched on?
- Is it clear to reviewers who will see their answers?
- Does the subject copy hide enough, or too much?
- Is anything on the dashboard confusing or missing?

Please note the page, what you did and what you expected to happen. A
screenshot helps.
