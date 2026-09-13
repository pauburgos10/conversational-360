"""Roll answers up into a cell tree.

A *cell* is one publishable number: a mean over a set of answers, plus the
metadata suppression needs to decide whether it may be published at all.

The tree is item -> competency -> dimension -> overall, computed twice: once
scoped to a subject (feeding the individual report) and once scoped to a cohort
(feeding the team report). Suppression walks this same tree, which is what makes
complementary suppression expressible at all — you cannot reason about "sibling"
without a parent link.

Means are flat over ANSWERS rather than over per-ballot averages. That makes the
dominance rule meaningful: a ballot's share of a cell is literally the share of
the numbers behind the figure that came from that one rater.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import banks

LEVEL_ITEM = "item"
LEVEL_COMPETENCY = "competency"
LEVEL_DIMENSION = "dimension"
LEVEL_OVERALL = "overall"

SCOPE_SUBJECT = "subject"
SCOPE_COHORT = "cohort"


@dataclass
class Cell:
    key: str
    scope: str
    scope_id: str
    level: str
    level_id: str
    label: str
    parent_key: str | None
    values: list[tuple[str, float]] = field(default_factory=list)  # (ballot_id, value)

    @property
    def n_ballots(self) -> int:
        return len({b for b, _ in self.values})

    @property
    def n_answers(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float | None:
        if not self.values:
            return None
        return sum(v for _, v in self.values) / len(self.values)

    @property
    def ballot_shares(self) -> dict[str, float]:
        if not self.values:
            return {}
        counts: dict[str, int] = {}
        for ballot_id, _ in self.values:
            counts[ballot_id] = counts.get(ballot_id, 0) + 1
        total = len(self.values)
        return {b: c / total for b, c in counts.items()}

    @property
    def max_share(self) -> float:
        shares = self.ballot_shares
        return max(shares.values()) if shares else 0.0


def _key(scope: str, scope_id: str, level: str, level_id: str) -> str:
    return f"{scope}:{scope_id}|{level}:{level_id}"


def build_cells(round_data: dict, bank: banks.Bank | None = None) -> dict[str, Cell]:
    """round_data comes from store.fetch_round()."""
    bank = bank or banks.load_bank()
    ballot_subject = {b["ballot_id"]: b["subject_id"] for b in round_data["ballots"]}
    subjects = round_data["subjects"]

    item_comp = {i.id: i.competency_id for c in bank.competencies for i in c.items}
    item_label = {i.id: f"{i.competency_id} · {i.label}" for c in bank.competencies for i in c.items}
    comp_dim = {c.id: c.dimension for c in bank.competencies}
    comp_name = {c.id: c.name for c in bank.competencies}
    dim_name = {d["id"]: d["label"] for d in bank.dimensions}

    cells: dict[str, Cell] = {}

    def ensure(scope: str, scope_id: str, level: str, level_id: str,
               label: str, parent_key: str | None) -> Cell:
        k = _key(scope, scope_id, level, level_id)
        if k not in cells:
            cells[k] = Cell(k, scope, scope_id, level, level_id, label, parent_key)
        return cells[k]

    for row in round_data["likert"]:
        ballot_id = row["ballot_id"]
        if ballot_id not in ballot_subject:
            continue
        item_id = row["item_id"]
        value = float(row["value"])
        comp_id = item_comp.get(item_id)
        if comp_id is None:
            continue
        dim_id = comp_dim[comp_id]

        subject_id = ballot_subject[ballot_id]
        cohort = subjects.get(subject_id, {}).get("cohort", "unknown")

        for scope, scope_id in ((SCOPE_SUBJECT, subject_id), (SCOPE_COHORT, cohort)):
            overall = ensure(scope, scope_id, LEVEL_OVERALL, "all", "Overall", None)
            dim = ensure(scope, scope_id, LEVEL_DIMENSION, dim_id,
                         dim_name.get(dim_id, dim_id), overall.key)
            comp = ensure(scope, scope_id, LEVEL_COMPETENCY, comp_id,
                          comp_name.get(comp_id, comp_id), dim.key)
            item = ensure(scope, scope_id, LEVEL_ITEM, item_id,
                          item_label.get(item_id, item_id), comp.key)
            for cell in (overall, dim, comp, item):
                cell.values.append((ballot_id, value))

    return cells


def children_of(cells: dict[str, Cell], parent_key: str) -> list[Cell]:
    return [c for c in cells.values() if c.parent_key == parent_key]


def band_for(mean: float, rules: dict) -> str:
    for band in rules["bands"]:
        if "max_exclusive" in band and mean < band["max_exclusive"]:
            return band["label"]
        if "max_inclusive" in band and mean <= band["max_inclusive"]:
            return band["label"]
    return rules["bands"][-1]["label"]
