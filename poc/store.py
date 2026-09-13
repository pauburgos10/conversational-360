"""The response store.

This is `responses.db` from PLAN.md section 3. The single most important fact
about this schema is what is *missing*: there is no `rater_id` column, no
`relationship_to_subject`, and no foreign key to the invite table. Identity
lives in a separate database that this module cannot see.

`test_identity_separation.py` asserts that property against the live schema, so
adding such a column later fails the build rather than silently leaking.

`cohort` hangs off the SUBJECT, never off the ballot — the subject's team is not
a secret, whereas the rater's team is a re-identification vector when cohorts
are small.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
-- Subjects are NOT anonymous. A development report is useless if you cannot
-- tell whose it is, and the subject has consented to being assessed. Only the
-- RATER side of the boundary is anonymised. Name and role live here so reports
-- can identify the person the conversation is about.
CREATE TABLE IF NOT EXISTS subject (
    subject_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    cohort       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ballot (
    ballot_id    TEXT PRIMARY KEY,
    subject_id   TEXT NOT NULL REFERENCES subject(subject_id),
    round_id     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL,
    submitted_at TEXT
);

CREATE TABLE IF NOT EXISTS likert_answer (
    ballot_id TEXT NOT NULL REFERENCES ballot(ballot_id),
    item_id   TEXT NOT NULL,
    value     INTEGER NOT NULL CHECK (value BETWEEN 1 AND 3),
    PRIMARY KEY (ballot_id, item_id)
);

CREATE TABLE IF NOT EXISTS open_answer (
    ballot_id       TEXT NOT NULL REFERENCES ballot(ballot_id),
    ref             TEXT NOT NULL,
    competency_id   TEXT,
    theme           TEXT,
    -- What the rater actually wrote. HR reads this as written (PLAN.md §2);
    -- masking for the subject's copy happens at render time in `poc.reporting`.
    text            TEXT NOT NULL,
    -- Which redaction rules WILL fire when this is rendered for the subject.
    -- Stored rather than recomputed so the rater's pre-submit preview and the
    -- subject's report cannot drift apart.
    redaction_codes TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (ballot_id, ref)
);

-- Items a rater marked "not observed". Separate from `likert_answer` because a
-- decline is not a score: there is no value to put in the `value` column that
-- would not be a lie, and any sentinel there would eventually be averaged by
-- something that forgot to filter it.
--
-- Worth storing rather than simply omitting the row, because the two are not the
-- same fact. An absent item could mean "not asked" (the bank changed mid-round)
-- or "could not judge", and only the second one tells you something about the
-- subject: "four of six reviewers had no basis to assess Decision-Making" is a
-- finding about how visible that person's decisions are.
CREATE TABLE IF NOT EXISTS declined_item (
    ballot_id TEXT NOT NULL REFERENCES ballot(ballot_id),
    item_id   TEXT NOT NULL,
    PRIMARY KEY (ballot_id, item_id)
);

-- Code and timestamp only. The text that triggered a guardrail is never stored.
CREATE TABLE IF NOT EXISTS guardrail_event (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ballot_id TEXT NOT NULL REFERENCES ballot(ballot_id),
    code      TEXT NOT NULL,
    at        TEXT NOT NULL
);

-- The self-assessment is NOT a ballot and never enters the anonymous pool.
--
-- Two reasons. Semantically the client treats it as a separate perspective:
-- "poniendo foco en la autoevaluación y la evaluación del reporte directo" —
-- it is a comparison point for the development conversation, explicitly not part
-- of the average. And structurally, flagging one ballot in a pool of three as
-- "the self one" would identify the remaining raters by elimination, which is
-- exactly what round 1 forbids. Keeping it in its own table means the anonymous
-- pool never has to carry a role marker at all.
--
-- This table IS attributable, deliberately: it holds the subject's own answers
-- about themselves, and they already know what they said.
CREATE TABLE IF NOT EXISTS self_assessment (
    subject_id   TEXT NOT NULL REFERENCES subject(subject_id),
    round_id     TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    value        INTEGER NOT NULL CHECK (value BETWEEN 1 AND 3),
    submitted_at TEXT NOT NULL,
    PRIMARY KEY (subject_id, round_id, item_id)
);

CREATE TABLE IF NOT EXISTS round_freeze (
    round_id   TEXT PRIMARY KEY,
    frozen_at  TEXT NOT NULL,
    ballot_n   INTEGER NOT NULL,
    digest     TEXT NOT NULL
);
"""

FORBIDDEN_COLUMNS = {
    "rater_id", "rater", "relationship", "relationship_to_subject",
    "invite_id", "invite_token", "token", "email", "rater_cohort", "ip",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path: Path | None = None):
    conn = sqlite3.connect(path or config.RESPONSES_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def upsert_subject(subject_id: str, display_name: str, cohort: str,
                   role: str = "", path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO subject(subject_id, display_name, role, cohort) VALUES(?,?,?,?) "
            "ON CONFLICT(subject_id) DO UPDATE SET display_name=excluded.display_name, "
            "role=excluded.role, cohort=excluded.cohort",
            (subject_id, display_name, role, cohort),
        )


def create_ballot(subject_id: str, round_id: str, ballot_id: str | None = None,
                  path: Path | None = None) -> str:
    bid = ballot_id or uuid.uuid4().hex
    with connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ballot(ballot_id, subject_id, round_id, created_at) VALUES(?,?,?,?)",
            (bid, subject_id, round_id, now()),
        )
    return bid


def save_submission(ballot_id: str, likert: dict[str, int], open_answers: list[dict],
                    guardrail_codes: list[str], declined: list[str] | None = None,
                    path: Path | None = None) -> None:
    with connect(path) as conn:
        for item_id in (declined or []):
            # Guard against an item arriving as both. `collect` already keeps the
            # two disjoint, but this row would quietly deflate a competency's
            # coverage if that ever stopped being true.
            if item_id in likert:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO declined_item(ballot_id, item_id) VALUES(?,?)",
                (ballot_id, item_id),
            )
        for item_id, value in likert.items():
            conn.execute(
                "INSERT OR REPLACE INTO likert_answer(ballot_id, item_id, value) VALUES(?,?,?)",
                (ballot_id, item_id, int(value)),
            )
        for a in open_answers:
            conn.execute(
                "INSERT OR REPLACE INTO open_answer"
                "(ballot_id, ref, competency_id, theme, text, redaction_codes) VALUES(?,?,?,?,?,?)",
                (ballot_id, a["ref"], a.get("competency_id"), a.get("theme"),
                 a["text"], json.dumps(a.get("codes", []))),
            )
        for code in guardrail_codes:
            conn.execute(
                "INSERT INTO guardrail_event(ballot_id, code, at) VALUES(?,?,?)",
                (ballot_id, code, now()),
            )
        conn.execute(
            "UPDATE ballot SET status='submitted', submitted_at=? WHERE ballot_id=?",
            (now(), ballot_id),
        )


def save_self_assessment(subject_id: str, round_id: str, likert: dict[str, int],
                         path: Path | None = None) -> None:
    """Record the subject's own ratings. Never counted in any aggregate."""
    with connect(path) as conn:
        for item_id, value in likert.items():
            conn.execute(
                "INSERT OR REPLACE INTO self_assessment"
                "(subject_id, round_id, item_id, value, submitted_at) VALUES(?,?,?,?,?)",
                (subject_id, round_id, item_id, int(value), now()),
            )


def freeze_round(round_id: str, path: Path | None = None) -> dict:
    """Close the window and record an immutable digest of the dataset."""
    import hashlib

    with connect(path) as conn:
        rows = conn.execute(
            "SELECT b.ballot_id, l.item_id, l.value FROM ballot b "
            "JOIN likert_answer l ON l.ballot_id=b.ballot_id "
            "WHERE b.round_id=? AND b.status='submitted' ORDER BY b.ballot_id, l.item_id",
            (round_id,),
        ).fetchall()
        opens = conn.execute(
            "SELECT o.ballot_id, o.ref, o.text FROM ballot b "
            "JOIN open_answer o ON o.ballot_id=b.ballot_id "
            "WHERE b.round_id=? AND b.status='submitted' ORDER BY o.ballot_id, o.ref",
            (round_id,),
        ).fetchall()
        n = conn.execute(
            "SELECT COUNT(*) c FROM ballot WHERE round_id=? AND status='submitted'", (round_id,)
        ).fetchone()["c"]

        h = hashlib.sha256()
        for r in rows:
            h.update(f"{r['ballot_id']}|{r['item_id']}|{r['value']}\n".encode())
        for r in opens:
            h.update(f"{r['ballot_id']}|{r['ref']}|{r['text']}\n".encode())
        digest = h.hexdigest()

        conn.execute(
            "INSERT OR REPLACE INTO round_freeze(round_id, frozen_at, ballot_n, digest) VALUES(?,?,?,?)",
            (round_id, now(), n, digest),
        )
        conn.execute("UPDATE ballot SET status='frozen' WHERE round_id=? AND status='submitted'", (round_id,))
    return {"round_id": round_id, "ballot_n": n, "digest": digest}


def fetch_round(round_id: str, path: Path | None = None) -> dict:
    """Everything the aggregation layer is allowed to see."""
    with connect(path) as conn:
        subjects = {
            r["subject_id"]: dict(r)
            for r in conn.execute("SELECT * FROM subject").fetchall()
        }
        ballots = [
            dict(r) for r in conn.execute(
                "SELECT ballot_id, subject_id FROM ballot WHERE round_id=? AND status IN ('submitted','frozen')",
                (round_id,),
            ).fetchall()
        ]
        ids = {b["ballot_id"] for b in ballots}
        likert = [
            dict(r) for r in conn.execute("SELECT ballot_id, item_id, value FROM likert_answer").fetchall()
            if r["ballot_id"] in ids
        ]
        opens = [
            dict(r) for r in conn.execute(
                "SELECT ballot_id, ref, competency_id, theme, text FROM open_answer"
            ).fetchall()
            if r["ballot_id"] in ids
        ]
        # Returned alongside, never merged into `likert`: every aggregate is
        # built from that key, and a decline must not be able to reach one.
        declined = [
            dict(r) for r in conn.execute(
                "SELECT ballot_id, item_id FROM declined_item"
            ).fetchall()
            if r["ballot_id"] in ids
        ]
        # Returned in a separate key, not merged into `likert`, so no aggregation
        # code can pick it up by accident.
        self_rows = conn.execute(
            "SELECT subject_id, item_id, value FROM self_assessment WHERE round_id=?",
            (round_id,),
        ).fetchall()
        self_assessments: dict[str, dict[str, int]] = {}
        for row in self_rows:
            self_assessments.setdefault(row["subject_id"], {})[row["item_id"]] = row["value"]

    return {"subjects": subjects, "ballots": ballots, "likert": likert, "open": opens,
            "declined": declined, "self_assessments": self_assessments}


def schema_columns(path: Path | None = None) -> dict[str, list[str]]:
    with connect(path) as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        return {t: [r["name"] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()] for t in tables}
