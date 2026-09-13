"""Qualitative themes: threshold and the no-verbatim guarantee."""

from poc.aggregation import themes as themes_mod
from poc.llm import StubLLM, Theme, contains_verbatim


class LeakyLLM:
    """Stands in for a model that ignores 'paraphrase, do not quote'."""

    def cluster_themes(self, records):
        return [Theme("leak", "Leaked", records[0][1], "strength", tuple(b for b, _ in records))]


def records(n, text="They documented the runbook and handed it over cleanly last month"):
    return [(f"b{i}", text) for i in range(n)]


def test_theme_below_threshold_is_not_reported(rules):
    found, diag = themes_mod.extract(records(2), rules, StubLLM())
    assert found == []
    assert diag["dropped_min_n"] >= 1


def test_theme_at_threshold_is_reported(rules):
    found, _ = themes_mod.extract(records(3), rules, StubLLM())
    assert found and all(t.n >= 3 for t in found)


def test_verbatim_output_is_dropped_even_when_the_threshold_is_met(rules):
    found, diag = themes_mod.extract(records(8), rules, LeakyLLM())
    assert found == [], "a quoted rater must never reach a report"
    assert diag["dropped_verbatim"] == 1


def test_verbatim_can_be_allowed_by_configuration(rules):
    rules["themes"]["allow_verbatim_quotes"] = True
    found, _ = themes_mod.extract(records(8), rules, LeakyLLM())
    assert len(found) == 1, "the guarantee is a policy switch, and it is off by default"


def test_default_rules_forbid_verbatim():
    from poc import config
    assert config.rules()["themes"]["allow_verbatim_quotes"] is False


def test_reported_themes_are_capped(rules):
    rules["themes"]["max_themes_per_report"] = 2
    mixed = [(f"b{i}", t) for i in range(6) for t in (
        "they hit every deadline reliably",
        "I have to remind them repeatedly",
        "they documented the process",
        "they automated the weekly report",
    )]
    found, _ = themes_mod.extract(mixed, rules, StubLLM())
    assert len(found) <= 2


def test_ngram_check_ignores_short_overlaps():
    assert not contains_verbatim("they were reliable", ["they were reliable and clear"])
    assert contains_verbatim(
        "they caught the vendor delay in week two",
        ["last quarter they caught the vendor delay in week two and re-planned"],
    )


def test_themes_carry_a_valence_for_report_grouping(rules):
    found, _ = themes_mod.extract(
        records(5, "I usually have to remind them twice before a task closes out"), rules, StubLLM()
    )
    assert found and {t.valence for t in found} <= {"strength", "opportunity", "neutral"}
