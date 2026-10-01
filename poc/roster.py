"""The subject roster — who is being reviewed.

The brief's "Minimum Inputs Provided" lists a *rater* roster and no subject
roster, which is an omission rather than a decision: you cannot run a round
without knowing whose development it is for. `inputs/subjects.csv` fills that
gap, and it is the one input file an administrator must edit before a real
round.

Subjects are NOT anonymous — see PLAN.md §3. Their name and role appear on every
report, because a development conversation needs to be attributable to a person.
Only the rater side of the boundary is anonymised.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from . import config
from .graph.redaction import _derive

REQUIRED_COLUMNS = ["subject_id", "display_name", "role", "cohort"]
# A loose shape check, the same one the invite form applies: it catches the typo
# that would leave a link unsendable, without pretending to verify the address.
EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]{2,}")
# Optional so a roster written before reporting lines existed still loads — the
# live copy on a deployment's disk is one, and it is never overwritten.
OPTIONAL_COLUMNS = ["manager_id", "email"]
COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


@dataclass(frozen=True)
class Subject:
    """One person on the roster.

    `role` is the job title and `cohort` the department — the column keeps its
    old name so existing files load. `manager_id` is the `subject_id` of who they
    report to, empty at the top of the organisation; peers are the people who
    share it.

    `email` is optional and only ever a suggestion: when this person is proposed
    as someone's reviewer, HR's mint form starts from it. Links can still go to
    any address, including people outside the organisation.
    """
    subject_id: str
    display_name: str
    role: str
    cohort: str
    manager_id: str = ""
    email: str = ""


@lru_cache(maxsize=1)
def load_subjects(path: Path | None = None) -> tuple[Subject, ...]:
    path = path or config.SUBJECTS_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"{path} is missing. It lists who is being reviewed: "
            f"{','.join(REQUIRED_COLUMNS)}"
        )

    with Path(path).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        raise ValueError(f"{path} has a header but no subjects.")

    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        raise ValueError(f"{path} is missing column(s): {', '.join(missing)}")

    subjects = []
    seen: set[str] = set()
    for line_no, row in enumerate(rows, start=2):
        sid = (row["subject_id"] or "").strip()
        name = (row["display_name"] or "").strip()
        if not sid or not name:
            raise ValueError(f"{path} line {line_no}: subject_id and display_name are required.")
        if sid in seen:
            raise ValueError(f"{path} line {line_no}: duplicate subject_id {sid!r}.")
        seen.add(sid)
        subjects.append(Subject(sid, name, (row["role"] or "").strip(),
                                (row["cohort"] or "").strip() or "unassigned",
                                (row.get("manager_id") or "").strip(),
                                (row.get("email") or "").strip()))

    # Lenient on purpose: a hand-edited file naming a manager who is not on the
    # roster loses that line rather than failing the load. A failed load is not
    # contained — `redaction_names` would lose every subject's name, and free text
    # naming them would reach the subject's copy unmasked. `add_subject` is where
    # a bad reference is refused.
    known = {s.subject_id for s in subjects}
    return tuple(
        s if s.manager_id in known and s.manager_id != s.subject_id
        else replace(s, manager_id="")
        for s in subjects
    )


def redaction_names(path: Path | None = None) -> tuple[set[str], set[str]]:
    """Names the redactor removes from free text, as (explicit, derived).

    Two sources are merged so neither can be forgotten:

    * `inputs/name_gazetteer.txt` — anyone who might be *mentioned* in feedback,
      which is a superset of the people being reviewed: colleagues, former staff,
      external collaborators.
    * every subject's `display_name` — automatic, because a roster entry that is
      not in the gazetteer would leave that person's name intact in free text.

    A subject's own name is redacted from feedback about them. That is harmless
    over-redaction and simpler than carrying a per-report exemption.
    """
    explicit: set[str] = set()
    derived: set[str] = set()

    gazetteer = config.GAZETTEER_PATH
    if Path(gazetteer).exists():
        for line in Path(gazetteer).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                explicit.add(line)
                derived |= _derive(line)

    try:
        for subject in load_subjects(path):
            explicit.add(subject.display_name)
            derived |= _derive(subject.display_name)
    except (FileNotFoundError, ValueError):
        pass  # redaction must still work before a roster exists

    return explicit, derived - explicit


def redaction_roles(path: Path | None = None) -> set[str]:
    """The organisation's job titles, for masking a reviewer who names their own.

    "As the only business process analyst here…" identifies the writer as surely
    as a name does. The titles come from the roster for the same reason the names
    do: a list typed into the code covers the roles it was written for and no
    others.
    """
    try:
        return {s.role for s in load_subjects(path) if s.role}
    except (FileNotFoundError, ValueError):
        return set()  # redaction must still work before a roster exists


def next_subject_id(subjects: tuple[Subject, ...] | None = None,
                    reserved: set[str] | None = None,
                    path: Path | None = None) -> str:
    """The next free S-number.

    `reserved` lets a caller fold in ids that exist somewhere this module cannot
    see — the response store, most obviously, which can hold a subject whose
    roster row was removed. Reusing such an id would silently attach a new person
    to an old person's ballots.
    """
    try:
        subjects = subjects if subjects is not None else load_subjects(path)
    except (FileNotFoundError, ValueError):
        subjects = ()

    taken = {s.subject_id for s in subjects} | (reserved or set())
    highest = 0
    for subject_id in taken:
        match = re.fullmatch(r"S(\d+)", subject_id)
        if match:
            highest = max(highest, int(match.group(1)))

    candidate = highest + 1
    while f"S{candidate}" in taken:
        candidate += 1
    return f"S{candidate}"


def add_subject(display_name: str, role: str = "", cohort: str = "",
                subject_id: str | None = None, reserved: set[str] | None = None,
                path: Path | None = None, manager_id: str = "",
                email: str = "") -> Subject:
    """Append a subject to the roster CSV and invalidate the cached read.

    The CSV is written rather than the database alone, because this file is the
    roster of record: `seed_ballots` re-reads it after wiping `responses.db`, and
    `redaction_names()` derives the redaction list from it. A subject that exists
    only in the database would vanish on the next reseed and — the part that
    matters — their name would never be stripped from free text about them.

    Clearing the cache here is not tidiness. `load_subjects` is `lru_cache`d, so
    without it `redaction_names()` keeps returning the roster as it was at first
    read, and the caller rebuilds a redactor that has never heard of this person.
    """
    path = Path(path or config.SUBJECTS_PATH)
    display_name = (display_name or "").strip()
    role = (role or "").strip()
    cohort = (cohort or "").strip() or "unassigned"
    manager_id = (manager_id or "").strip()
    email = (email or "").strip()
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError(f"{email!r} does not look like an email address.")

    if not display_name:
        raise ValueError("A display name is required — it titles every report about this person.")

    try:
        existing = load_subjects(path)
    except (FileNotFoundError, ValueError):
        existing = ()

    if any(s.display_name.casefold() == display_name.casefold() for s in existing):
        raise ValueError(
            f"{display_name!r} is already on the roster. Two subjects with one name "
            "make their reports indistinguishable."
        )

    if manager_id and not any(s.subject_id == manager_id for s in existing):
        raise ValueError(f"The manager {manager_id!r} is not on the roster.")

    subject_id = (subject_id or next_subject_id(existing, reserved, path)).strip()
    if any(s.subject_id == subject_id for s in existing):
        raise ValueError(f"subject_id {subject_id!r} is already taken.")

    # Rewritten whole rather than appended to: a roster from before `manager_id`
    # existed has a four-column header, and appending a five-column row to it
    # would leave the new column unnamed. Every existing row is carried over as
    # read, so nothing an earlier edit wrote is lost.
    new = Subject(subject_id, display_name, role, cohort, manager_id, email)
    rows = [*_raw_rows(path), {
        "subject_id": new.subject_id, "display_name": new.display_name,
        "role": new.role, "cohort": new.cohort, "manager_id": new.manager_id,
        "email": new.email,
    }]

    # csv.DictWriter rather than an f-string: a display name containing a comma
    # would otherwise write a row that parses back as two columns.
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: (row.get(c) or "") for c in COLUMNS})
    path.write_text(buf.getvalue(), encoding="utf-8")

    load_subjects.cache_clear()
    return new


def _raw_rows(path: Path) -> list[dict]:
    """The file's rows exactly as written, for rewriting it without loss."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
