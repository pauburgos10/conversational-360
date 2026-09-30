"""The instrument must match the client's evaluation template, section by section.

`Pluralit Core Team - Compensation Structure 2026`, "Plantilla de Evaluación 360",
groups the scored competencies in two sections:

    Sección I  : Expertise Técnico            -> I. Capacidad de Delivery
    Sección II A: Competencias Blandas & Valores Pluralit
                                              -> II A.1 Nivel de Autonomía
                                                 II A.2 Madurez en Toma de Decisiones
                                                 II A.3 Innovación y Mejora
                                                 II A.4 Impacto Sistémico
                                                 II A.5 Orientación al cliente
    Sección II B: Evaluación Cualitativa      -> the four open questions

This was wrong for a long time: all six competencies were grouped as "core", so
the reports put Autonomy, Decision-Making, Innovation, Systemic Impact and
Customer Orientation in the 60% dimension when the client puts them in the 40%
one. These tests pin the mapping, the weighting, and the fact that the scored
part of the instrument contains nothing we invented.
"""

from __future__ import annotations

import pytest

from poc import banks
from poc.aggregation import rollup

SECTION_I = {"C1"}
SECTION_II_A = {"C2", "C3", "C4", "C5", "C6"}


@pytest.fixture
def bank():
    banks.load_bank.cache_clear()
    return banks.load_bank()


def test_the_two_dimensions_are_the_clients_sections(bank):
    dims = {d["id"]: d for d in bank.dimensions}
    assert set(dims) == {"delivery", "soft_skills"}
    assert dims["delivery"]["weight"] == 0.60
    assert dims["soft_skills"]["weight"] == 0.40


def test_delivery_is_the_only_competency_in_the_60_percent_dimension(bank):
    """The finding this file exists for."""
    by_dim: dict[str, set[str]] = {}
    for c in bank.competencies:
        by_dim.setdefault(c.dimension, set()).add(c.id)
    assert by_dim["delivery"] == SECTION_I
    assert by_dim["soft_skills"] == SECTION_II_A


@pytest.mark.parametrize("cid", sorted(SECTION_II_A))
def test_each_soft_skill_competency_is_not_marked_core(bank, cid):
    assert bank.competency(cid).dimension == "soft_skills"


def test_the_scored_instrument_contains_nothing_we_invented(bank):
    """B1-B4 were added on the premise that the 40% bucket was under-specified.

    Sección II A specifies five competencies and fifteen items, so the premise was
    wrong and the additions diluted the client's own model. The qualitative part
    of the instrument is Sección II B, which lives in `qualitative_fixed`.
    """
    assert {c.id for c in bank.competencies} == SECTION_I | SECTION_II_A
    assert len(bank.all_items) == 18
    assert len(bank.qualitative_fixed) == 4


def test_anchor_sets_match_the_documents_two_scales(bank):
    """The document defines levels for Delivery and for "Gestión e Impacto" only."""
    assert set(bank.anchors) == {"delivery", "management_impact"}
    assert set(bank.anchors_self) == set(bank.anchors)
    assert bank.competency("C1").anchor_set == "delivery"
    for cid in SECTION_II_A:
        assert bank.competency(cid).anchor_set == "management_impact"


def test_c5_second_item_follows_the_evaluation_table(bank):
    """The document contradicts itself; the template is the instrument, so it wins.

    Narrative section 5 says "Entusiasmo y clima laboral"; the evaluation table
    says "Orientación a resultados". We follow the table.
    """
    item = bank.item("C5.2")
    assert item.label == "Results Orientation"
    assert "highest standards" in item.prompt
    assert "energetic" not in item.prompt.lower()


def test_every_probed_competency_still_has_probe_wording(bank):
    """Removing B1-B4 must not leave a competency without its open probes."""
    for c in bank.competencies:
        assert c.id in bank.open_probes, f"{c.id} has no probe wording"
    assert set(bank.open_probes) == SECTION_I | SECTION_II_A, "probes for removed competencies"


# --------------------------------------------------------------- the weighting

def _round(delivery: float, soft: float) -> dict:
    """A one-ballot round where each dimension has a known flat mean."""
    bank = banks.load_bank()
    likert = [
        {"ballot_id": "b1", "item_id": i.id,
         "value": delivery if bank.competency(i.competency_id).dimension == "delivery" else soft}
        for i in bank.all_items
    ]
    return {"subjects": {"S1": {"cohort": "x", "display_name": "S"}},
            "ballots": [{"ballot_id": "b1", "subject_id": "S1"}],
            "likert": likert, "open": [], "declined": [], "self_assessments": {}}


def test_the_overall_applies_60_40_and_is_not_a_pool_of_items(bank):
    """Pooled, Delivery's three items of eighteen would carry ~12% of the say."""
    cells = rollup.build_cells(_round(delivery=3, soft=1), bank)
    overall = cells["subject:S1|overall:all"]
    assert overall.mean == pytest.approx(0.6 * 3 + 0.4 * 1)      # 2.2
    pooled = sum(v for _, v in overall.values) / len(overall.values)
    assert pooled == pytest.approx((3 * 3 + 15 * 1) / 18)        # 1.33 — what it used to be
    assert overall.mean != pytest.approx(pooled)


def test_suppression_still_sees_the_answers_behind_the_weighted_figure(bank):
    """The weighting changes the arithmetic on top, not the evidence beneath."""
    cells = rollup.build_cells(_round(delivery=3, soft=1), bank)
    overall = cells["subject:S1|overall:all"]
    assert overall.n_answers == 18
    assert overall.n_ballots == 1
    assert overall.max_share == pytest.approx(1.0)


def test_weights_renormalise_when_a_dimension_has_no_answers(bank):
    """Every Delivery item declined must not read as a low score."""
    data = _round(delivery=3, soft=2)
    data["likert"] = [r for r in data["likert"]
                      if bank.competency(bank.item(r["item_id"]).competency_id).dimension
                      != "delivery"]
    overall = rollup.build_cells(data, bank)["subject:S1|overall:all"]
    assert overall.mean == pytest.approx(2.0), "should equal the surviving dimension"
