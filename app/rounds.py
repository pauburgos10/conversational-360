"""Round-level views the web layer needs, built from the existing pipeline.

Nothing here re-implements aggregation. `pipeline()` calls the same three
functions `poc/cli_report.py` calls, in the same order, so the browser and the
CLI cannot report different numbers.

`subject_state()` exists for one reason: at `min_n = 2` a subject with one
rater response renders "Not enough responses to report", which is correct and
looks exactly like a bug. Showing the ballot count next to the threshold turns
an apparent failure into a visible rule.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass

from poc import banks, config, store
from poc.aggregation import rollup, suppression
from poc.graph.build import build_app

DEFAULT_ROUND = "2026-H2"
INVITES_PATH = config.DATA / "invites.json"


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pipeline:
    round_id: str
    data: dict
    cells: dict
    marks: dict
    rules: dict
    bank: banks.Bank

    @property
    def summary(self) -> dict[str, int]:
        return suppression.summary(self.marks)

    @property
    def cohorts(self) -> list[str]:
        return sorted({s["cohort"] for s in self.data["subjects"].values()})


def pipeline(round_id: str = DEFAULT_ROUND) -> Pipeline:
    rules = config.rules()
    bank = banks.load_bank()
    data = store.fetch_round(round_id)
    cells = rollup.build_cells(data, bank)
    marks = suppression.evaluate(cells, rules)
    return Pipeline(round_id, data, cells, marks, rules, bank)


# ---------------------------------------------------------------------------
# the subject state panel
# ---------------------------------------------------------------------------

def subject_state(round_id: str = DEFAULT_ROUND) -> list[dict]:
    """Per subject: how many ballots have landed, and whether that clears min_n.

    Counts submitted *and* frozen ballots, matching `store.fetch_round`, because
    freezing a round must not make the panel disagree with the reports.
    """
    rules = config.rules()
    min_n = int(rules.get("min_n", 2))
    store.init()

    with store.connect() as conn:
        subjects = conn.execute(
            "SELECT subject_id, display_name, role, cohort FROM subject ORDER BY subject_id"
        ).fetchall()
        counts = {
            r["subject_id"]: r["n"]
            for r in conn.execute(
                "SELECT subject_id, COUNT(*) n FROM ballot "
                "WHERE round_id=? AND status IN ('submitted','frozen') GROUP BY subject_id",
                (round_id,),
            ).fetchall()
        }
        # Item ids, not a DISTINCT subject list: the panel now reports how far
        # each self-assessment has got, and a boolean "has answered something"
        # cannot tell a finished form from a single tapped radio button.
        self_counts: dict[str, dict[str, int]] = {}
        for r in conn.execute(
            "SELECT subject_id, item_id, value FROM self_assessment WHERE round_id=?",
            (round_id,),
        ).fetchall():
            self_counts.setdefault(r["subject_id"], {})[r["item_id"]] = r["value"]

    # In-progress interviews have no row in `ballot` — they exist only as
    # checkpoints until the rater submits. Counting `status='open'` here returned
    # zero for every subject, always.
    live: dict[str, int] = {}
    for state in ballot_states(round_id).values():
        if state.status == BALLOT_IN_PROGRESS and state.subject_id:
            live[state.subject_id] = live.get(state.subject_id, 0) + 1

    rows = []
    for s in subjects:
        n = counts.get(s["subject_id"], 0)
        self_state = self_status(s["subject_id"], round_id,
                                 answered=self_counts.get(s["subject_id"], {}))
        rows.append({
            "subject_id": s["subject_id"],
            "display_name": s["display_name"],
            "role": s["role"],
            "cohort": s["cohort"],
            "n_ballots": n,
            "n_open": live.get(s["subject_id"], 0),
            "min_n": min_n,
            "publishable": n >= min_n,
            "needed": max(0, min_n - n),
            "self": self_state,
            # Kept as the plain "is there anything at all" flag. It now means
            # started rather than finished, which is why nothing should render it
            # as "done" — `self.status` is the field with that answer.
            "has_self": self_state["started"],
        })
    return rows


def round_freeze_info(round_id: str = DEFAULT_ROUND) -> dict | None:
    store.init()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT round_id, frozen_at, ballot_n, digest FROM round_freeze WHERE round_id=?",
            (round_id,),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# pre-minted ballot links — the stand-in for invite distribution AND identity
# ---------------------------------------------------------------------------
#
# This file is the identity side of the boundary PLAN.md §3 describes. In the
# full build it is `identity.db`; here it is a JSON file doing the same job, and
# it holds the only reviewer identity the system has.
#
# It does two things, and after the client's demo-2 clarification the second one
# is deliberate rather than accidental:
#
#   1. distribution — which links exist, for which subject, handed to whom, and
#      how far each has got, so HR can chase the people who have not finished;
#   2. attribution — `ballot_id` is the join key into `responses.db`, so an
#      assigned address resolves to that reviewer's answers. `rater_labels()` is
#      the function that performs it, and the HR copy of a report is the only
#      thing that calls it.
#
# An earlier version of this comment claimed the file "cannot leak a rater
# identity because it never learns one". That stopped being true the moment
# addresses were attached, and the join it denied is now the requested feature.
#
# What the split still buys — and it is not nothing:
#
#   * `responses.db` holds no name, address, role or relationship in any column,
#     so that file alone discloses no reviewer. `tests/test_identity_separation`
#     asserts it against the live schema.
#   * The engine (`poc/`) cannot attribute on its own. `poc.reporting` never
#     reads this file; a caller holding both halves passes the mapping in. Every
#     attribution path is therefore one somebody wrote on purpose.
#   * Relationship and cohort still never travel with a response, so even an
#     attributed report cannot be broken down by role — which is the part of the
#     brief's anonymity requirement that survives this change intact.
#   * The subject's copy of a report resolves none of it.

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}$")


@dataclass(frozen=True)
class Invite:
    """One minted link, plus who it was handed to.

    `email` is empty for a link minted without an assignee — the original
    behaviour, still valid, and still the anonymous-by-default option.
    """

    ballot_id: str
    email: str = ""

    @property
    def assigned(self) -> bool:
        return bool(self.email)


def normalise_email(email: str) -> str:
    """Trim and validate, or raise. An empty string is allowed and means unassigned.

    Deliberately a loose check. The strict thing to do would be to verify the
    address exists, which needs a mail round trip this POC has no business making;
    the useful thing is to catch the typo that leaves HR waiting on a reminder
    that was never deliverable.
    """
    email = (email or "").strip()
    if not email:
        return ""
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValueError(f"{email!r} does not look like an email address.")
    return email


def _load_invites() -> dict:
    if not INVITES_PATH.exists():
        return {}
    try:
        return json.loads(INVITES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_invites(data: dict) -> None:
    INVITES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _as_invite(entry: str | dict) -> Invite:
    """One stored entry to an `Invite`, tolerating the pre-email format.

    Links minted before assignment existed are bare strings. Reading them as
    unassigned invites means an existing round keeps working and its links keep
    resolving, rather than a file written last week becoming unreadable.
    """
    if isinstance(entry, str):
        return Invite(ballot_id=entry)
    return Invite(ballot_id=entry.get("ballot_id", ""), email=entry.get("email", ""))


def _as_entry(invite: Invite) -> dict:
    return {"ballot_id": invite.ballot_id, "email": invite.email}


def mint_ballot_id() -> str:
    return f"w-{secrets.token_urlsafe(8)}"


def invites_for(round_id: str = DEFAULT_ROUND) -> dict[str, tuple[Invite, ...]]:
    return {
        subject_id: tuple(_as_invite(e) for e in entries)
        for subject_id, entries in _load_invites().get(round_id, {}).items()
    }


def mint_invites(subject_id: str, n: int = 1, round_id: str = DEFAULT_ROUND,
                 email: str = "") -> list[str]:
    """Mint `n` links for a subject, optionally all assigned to one address.

    Assigning one address to several links is allowed: a manager who owes two
    reviews gets two links, and both show against their name.
    """
    email = normalise_email(email)
    data = _load_invites()
    bucket = data.setdefault(round_id, {}).setdefault(subject_id, [])
    minted = [Invite(mint_ballot_id(), email) for _ in range(max(1, n))]
    bucket.extend(_as_entry(i) for i in minted)
    _save_invites(data)
    return [i.ballot_id for i in minted]


def assign_email(ballot_id: str, email: str, round_id: str = DEFAULT_ROUND) -> Invite:
    """Attach (or, with an empty string, detach) an address on an existing link.

    Reassignment is allowed and overwrites. The alternative — locking an address
    once set — would leave HR unable to correct a typo on a link already sent,
    and the link is the thing that matters, not the row.
    """
    email = normalise_email(email)
    data = _load_invites()
    for entries in data.get(round_id, {}).values():
        for index, entry in enumerate(entries):
            invite = _as_invite(entry)
            if invite.ballot_id == ballot_id:
                updated = Invite(ballot_id, email)
                entries[index] = _as_entry(updated)
                _save_invites(data)
                return updated
    raise LookupError(f"No minted link {ballot_id!r} in round {round_id!r}.")


def rater_labels(round_id: str = DEFAULT_ROUND) -> dict[str, str]:
    """`ballot_id -> who the link was assigned to`, for HR-attributed reports.

    This is the join the response store deliberately cannot make on its own. It
    lives here, on the identity side, and is passed *into* `poc.reporting` rather
    than looked up by it — so the engine stays unable to attribute anything unless
    a caller that holds both halves hands it the mapping.

    Links with no assignee are absent rather than blank, so the report can say
    "unassigned link" and make the gap visible.
    """
    return {
        invite.ballot_id: invite.email
        for invites in invites_for(round_id).values()
        for invite in invites
        if invite.email
    }


def subject_for_ballot(ballot_id: str, round_id: str = DEFAULT_ROUND) -> str | None:
    """Which subject a pre-minted link belongs to, if it came from the invite list."""
    for subject_id, invites in invites_for(round_id).items():
        if any(i.ballot_id == ballot_id for i in invites):
            return subject_id
    return None


# ---------------------------------------------------------------------------
# what a ballot link is actually doing
# ---------------------------------------------------------------------------
#
# `responses.db` cannot answer this. A ballot row is created only in the `finish`
# node, and only for a rater who submitted — so the store's `status` column is
# 'submitted' or 'frozen' and never anything else. Its 'open' default is
# unreachable, and a rater twenty questions in has no row at all.
#
# The live state lives in the checkpointer, keyed on the same ballot_id. Reading
# it crosses no boundary: a checkpoint holds redacted answers and no rater
# identity, exactly like the store.
#
# What is deliberately NOT distinguishable here:
#
#   * A rater who declined consent looks identical to one who never opened the
#     link. consent.md promises "Declining closes the session and records
#     nothing", and a page that lets HR tell the two apart makes that false.
#   * Pre-consent sits in the same bucket for the same reason. If opening the
#     link showed "in progress", then declining would visibly move it back, and
#     anyone watching the page would learn a decline had happened.
#   * A session halted by the crisis guardrail reads as "in progress" like any
#     other unfinished one. `guardrail_event` stores a code and never the text
#     precisely so distress cannot be attributed; surfacing it per link here
#     would undo that.

BALLOT_NOT_OPENED = "not opened"
BALLOT_IN_PROGRESS = "in progress"
BALLOT_SUBMITTED = "submitted"
BALLOT_FROZEN = "frozen"


@dataclass(frozen=True)
class BallotState:
    ballot_id: str
    status: str
    subject_id: str = ""
    answered: int = 0
    total: int = 0

    @property
    def started(self) -> bool:
        return self.status != BALLOT_NOT_OPENED

    @property
    def progress(self) -> str:
        return f"{self.answered}/{self.total}" if self.total else ""


def ballot_states(round_id: str = DEFAULT_ROUND) -> dict[str, BallotState]:
    """Every ballot this round knows about, submitted or still being answered.

    A ballot id absent from the result has not been started — or was declined,
    which by design reads the same way.
    """
    store.init()
    states: dict[str, BallotState] = {}

    with store.connect() as conn:
        for row in conn.execute(
            "SELECT ballot_id, subject_id, status FROM ballot WHERE round_id=?", (round_id,)
        ):
            states[row["ballot_id"]] = BallotState(
                ballot_id=row["ballot_id"],
                status=row["status"] if row["status"] in (BALLOT_FROZEN,) else BALLOT_SUBMITTED,
                subject_id=row["subject_id"],
            )

    total = len(banks.load_bank().all_items)
    app = build_app()

    # Drain the listing before asking for any state. `list()` yields from an open
    # cursor on the same sqlite connection `get_state` uses, and interleaving the
    # two makes the read hang rather than fail.
    thread_ids: list[str] = []
    for tup in app.checkpointer.list(None):
        ballot_id = tup.config["configurable"]["thread_id"]
        if ballot_id not in thread_ids:
            thread_ids.append(ballot_id)

    for ballot_id in thread_ids:
        if ballot_id in states:
            continue
        values = app.get_state({"configurable": {"thread_id": ballot_id}}).values
        if values.get("round_id") != round_id:
            continue
        # Consent is the line. Before it, and for a decline, this link is
        # indistinguishable from one nobody clicked.
        if not values.get("consent") or values.get("submitted"):
            continue

        states[ballot_id] = BallotState(
            ballot_id=ballot_id,
            status=BALLOT_IN_PROGRESS,
            subject_id=values.get("subject_id", ""),
            answered=len(values.get("likert", {})),
            total=total,
        )
    return states


def ballot_status(ballot_id: str, round_id: str = DEFAULT_ROUND) -> str:
    state = ballot_states(round_id).get(ballot_id)
    return state.status if state else BALLOT_NOT_OPENED


# ---------------------------------------------------------------------------
# the invite list, joined
# ---------------------------------------------------------------------------

def invite_rows(round_id: str = DEFAULT_ROUND) -> list[dict]:
    """Every minted link with its assignee and its live status, ready to render.

    The join lives here rather than in the router because it reads three sources
    that disagree about what a link is — `invites.json` knows who it was sent to,
    `ballot` knows if it came back, the checkpointer knows if it is underway —
    and a page that assembled that itself would be the second place to fix when
    any of the three changes.
    """
    subjects = {s["subject_id"]: s for s in subject_state(round_id)}
    minted = invites_for(round_id)
    states = ballot_states(round_id)

    rows = []
    for subject_id in sorted(subjects):
        for invite in minted.get(subject_id, ()):
            state = states.get(invite.ballot_id)
            rows.append({
                "subject_id": subject_id,
                "display_name": subjects[subject_id]["display_name"],
                "ballot_id": invite.ballot_id,
                "email": invite.email,
                "assigned": invite.assigned,
                "status": state.status if state else BALLOT_NOT_OPENED,
                "progress": state.progress if state else "",
                "complete": bool(state) and state.status in (BALLOT_SUBMITTED, BALLOT_FROZEN),
            })
    return rows


def invite_summary(rows: list[dict]) -> dict[str, int]:
    """Counts for the header line: how many are out, and how many are still owed.

    `outstanding` is the number HR actually acts on — assigned links that have
    not come back, i.e. the people there is someone to chase about.
    """
    return {
        "minted": len(rows),
        "assigned": sum(1 for r in rows if r["assigned"]),
        "unassigned": sum(1 for r in rows if not r["assigned"]),
        "complete": sum(1 for r in rows if r["complete"]),
        "in_progress": sum(1 for r in rows if r["status"] == BALLOT_IN_PROGRESS),
        "not_opened": sum(1 for r in rows if r["status"] == BALLOT_NOT_OPENED),
        "outstanding": sum(1 for r in rows if r["assigned"] and not r["complete"]),
    }


# ---------------------------------------------------------------------------
# the self-assessment
# ---------------------------------------------------------------------------
#
# Unlike a ballot, this one has no submit step — the form saves per page and the
# subject can revise until the round closes — so "done" cannot mean "they pressed
# the button". It means every item has a value, and anything between that and
# nothing is genuinely in progress: someone who answered four competencies and
# closed the tab is not a subject who has not started, and showing them as one
# had HR chasing people who were already halfway through.

SELF_NOT_STARTED = "not started"
SELF_IN_PROGRESS = "in progress"
SELF_COMPLETE = "complete"


def self_status(subject_id: str, round_id: str = DEFAULT_ROUND,
                answered: dict[str, int] | None = None) -> dict:
    """How far the subject has got with their own form.

    `answered` is an optional pre-read, so a caller looping over a whole roster
    can fetch every subject's rows in one query instead of one query per person.
    """
    answers = self_answers(subject_id, round_id) if answered is None else answered
    n_items = len(banks.load_bank().all_items)
    n_done = len(answers)

    if n_done == 0:
        status = SELF_NOT_STARTED
    elif n_done >= n_items:
        status = SELF_COMPLETE
    else:
        status = SELF_IN_PROGRESS

    return {
        "status": status,
        "n_answered": n_done,
        "n_items": n_items,
        "started": status != SELF_NOT_STARTED,
        "complete": status == SELF_COMPLETE,
        "progress": f"{n_done}/{n_items}",
    }


def self_answers(subject_id: str, round_id: str = DEFAULT_ROUND) -> dict[str, int]:
    """The subject's own ratings so far, for prefilling a partially completed form.

    The self-assessment form saves page by page rather than accumulating in
    hidden fields, so this doubles as the resume mechanism: reopening the form
    shows what was already answered.
    """
    store.init()
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT item_id, value FROM self_assessment WHERE subject_id=? AND round_id=?",
            (subject_id, round_id),
        ).fetchall()
    return {r["item_id"]: r["value"] for r in rows}
