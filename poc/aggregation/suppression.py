"""Small-N suppression.

Three rules, applied in order, until the result is stable:

1. **MIN_N** — fewer than `min_n` distinct ballots contributed. The obvious one.

2. **DOMINANCE** — one ballot supplied more than `dominance_max_share` of the
   answers behind the figure. A cell can clear min_n and still be effectively
   one person's opinion if the others skipped most of its items.

3. **COMPLEMENTARY** — the rule most implementations miss. If a parent figure is
   published and exactly one of its children is suppressed, the suppressed value
   is recoverable by arithmetic: it is the residual. Suppressing a second child
   is what actually protects the first. This runs to a fixed point, because
   suppressing that second child can leave *its* own sibling group with exactly
   one suppression.

The output marks every cell, published or not, with the reason. Reports render
the reason rather than a blank, so a reader can tell "too few responses" apart
from "no data".
"""

from __future__ import annotations

from dataclasses import dataclass

from .rollup import (
    LEVEL_COMPETENCY, LEVEL_DIMENSION, LEVEL_ITEM, LEVEL_OVERALL, Cell, children_of,
)

# Complementary suppression must settle a parent's own status before it judges
# that parent's children, otherwise the outcome depends on dict ordering: a
# child could be sacrificed to protect a sibling whose parent then turns out
# to be suppressed anyway. Processing strictly top-down removes that.
_DEPTH = {LEVEL_OVERALL: 0, LEVEL_DIMENSION: 1, LEVEL_COMPETENCY: 2, LEVEL_ITEM: 3}

CODE_MIN_N = "MIN_N"
CODE_DOMINANCE = "DOMINANCE"
CODE_COMPLEMENTARY = "COMPLEMENTARY"


@dataclass
class Mark:
    suppressed: bool
    code: str | None = None
    reason: str = ""


def evaluate(cells: dict[str, Cell], rules: dict) -> dict[str, Mark]:
    cfg = rules["suppression"]
    min_n = int(cfg["min_n"])
    max_share = float(cfg["dominance_max_share"])
    do_complementary = bool(cfg.get("complementary", True))
    codes = cfg.get("codes", {})

    marks: dict[str, Mark] = {}
    # Callers may key the dict however they like; parent links always use
    # Cell.key, so translate between the two rather than assuming they match.
    slot_of: dict[str, str] = {cell.key: slot for slot, cell in cells.items()}

    # --- rules 1 and 2 ----------------------------------------------------
    for key, cell in cells.items():
        if cell.n_ballots < min_n:
            marks[key] = Mark(
                True, CODE_MIN_N,
                codes.get("MIN_N", "Suppressed: fewer than {min_n} contributing responses")
                .format(min_n=min_n),
            )
        elif cell.max_share > max_share:
            marks[key] = Mark(
                True, CODE_DOMINANCE,
                codes.get("DOMINANCE", "Suppressed: one response accounts for more than {pct}% of this cell")
                .format(pct=int(max_share * 100)),
            )
        else:
            marks[key] = Mark(False)

    if not do_complementary:
        return marks

    # --- rule 3, to a fixed point ----------------------------------------
    complementary_reason = codes.get(
        "COMPLEMENTARY",
        "Suppressed: publishing this value would reveal a suppressed sibling",
    )

    changed = True
    guard = 0
    while changed:
        changed = False
        guard += 1
        if guard > len(cells) + 2:      # cannot loop more than once per cell
            break

        parent_cells = [
            cells[slot_of[key]]
            for key in {c.parent_key for c in cells.values() if c.parent_key}
            if key in slot_of
        ]
        parent_cells.sort(key=lambda c: (_DEPTH.get(c.level, 99), c.key))

        for parent in parent_cells:
            if marks[slot_of[parent.key]].suppressed:
                # A suppressed parent gives away no residual, so its children
                # need no extra protection from this rule.
                continue
            siblings = children_of(cells, parent.key)
            if len(siblings) < 2:
                continue
            hidden = [s for s in siblings if marks[slot_of[s.key]].suppressed]
            if len(hidden) != 1:
                continue
            visible = [s for s in siblings if not marks[slot_of[s.key]].suppressed]
            # Sacrifice the least informative sibling: fewest ballots, then
            # fewest answers, then a stable key ordering for reproducibility.
            victim = min(visible, key=lambda c: (c.n_ballots, c.n_answers, c.key))
            marks[slot_of[victim.key]] = Mark(True, CODE_COMPLEMENTARY, complementary_reason)
            changed = True

    return marks


def summary(marks: dict[str, Mark]) -> dict[str, int]:
    out = {"published": 0, CODE_MIN_N: 0, CODE_DOMINANCE: 0, CODE_COMPLEMENTARY: 0}
    for mark in marks.values():
        if not mark.suppressed:
            out["published"] += 1
        else:
            out[mark.code] = out.get(mark.code, 0) + 1
    return out
