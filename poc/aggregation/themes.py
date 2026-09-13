"""Qualitative themes.

Two protections sit on top of clustering, and both are enforced in code rather
than in a prompt:

* **min_ballots_per_theme** — a theme mentioned by one or two raters is a
  fingerprint, not a theme.
* **no verbatim** — even after names are stripped, a rater's own sentence
  identifies them by voice and by the specifics only they would know. Every
  emitted paraphrase is checked against every source answer for a shared
  6-word n-gram, and any theme that leaks is dropped. This runs in stub mode
  and in live mode: it protects against a model that ignores its instructions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import llm as llm_mod


@dataclass
class ReportedTheme:
    id: str
    label: str
    paraphrase: str
    valence: str
    n: int


def extract(records: list[tuple[str, str]], rules: dict,
            llm=None) -> tuple[list[ReportedTheme], dict[str, int]]:
    """records: (ballot_id, redacted_text). Returns (themes, diagnostics)."""
    cfg = rules["themes"]
    min_n = int(cfg["min_ballots_per_theme"])
    allow_verbatim = bool(cfg.get("allow_verbatim_quotes", False))
    cap = int(cfg.get("max_themes_per_report", 6))

    llm = llm or llm_mod.get_llm()
    raw = llm.cluster_themes(records)
    sources = [text for _b, text in records]

    diagnostics = {"clustered": len(raw), "dropped_min_n": 0, "dropped_verbatim": 0}
    kept: list[ReportedTheme] = []

    for theme in raw:
        if theme.n < min_n:
            diagnostics["dropped_min_n"] += 1
            continue
        if not allow_verbatim and llm_mod.contains_verbatim(theme.paraphrase, sources):
            # The model (or the lexicon) echoed a rater. Drop rather than emit.
            diagnostics["dropped_verbatim"] += 1
            continue
        kept.append(ReportedTheme(theme.id, theme.label, theme.paraphrase, theme.valence, theme.n))

    kept.sort(key=lambda t: (-t.n, t.id))
    diagnostics["reported"] = len(kept[:cap])
    return kept[:cap], diagnostics
