"""What produced each question, recorded truthfully.

The property under test is narrow and easy to state: a fallback must never be
reported as a live call. `OpenAILLM.probe` returns the bank template when the API
raises, byte-for-byte identical to what stub mode returns — so the returned text
carries no evidence of what happened, and the record is the only thing that does.

These tests fake the client rather than the network. Nothing here reaches out,
and `conftest.force_stub_mode` still pins the suite offline.
"""

from __future__ import annotations

import logging

import pytest

from poc import config, llm, trace


@pytest.fixture(autouse=True)
def clean_trace():
    trace.clear()
    yield
    trace.clear()


class _Boom:
    """A ChatOpenAI stand-in whose every call fails."""

    def __init__(self, exc=RuntimeError("upstream is down")):
        self.exc = exc

    def invoke(self, _prompt):
        raise self.exc

    def with_structured_output(self, _schema):
        return self


class _Answers:
    """A ChatOpenAI stand-in that returns wording, as a live call would."""

    def __init__(self, text="A reworded question?"):
        self.text = text
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        return type("Msg", (), {"content": self.text})()


def _openai_client(fake):
    client = llm.OpenAILLM.__new__(llm.OpenAILLM)
    client._llm = fake
    client._fallback = llm.StubLLM()
    client._model = "gpt-4o-mini"
    return client


# ---------------------------------------------------------------------------

def test_stub_calls_are_recorded_as_template_not_model():
    llm.StubLLM().probe("Thinking about {competency}?", "Delivery")
    call = trace.snapshot(1)[0]
    assert call.engine == trace.STUB
    assert call.outcome == trace.TEMPLATE
    assert call.is_live is False
    assert call.subject == "Delivery"


def test_a_live_call_is_recorded_as_a_model_call():
    fake = _Answers()
    text = _openai_client(fake).probe("Thinking about {competency}?", "Delivery")

    assert fake.calls == 1
    assert text == "A reworded question?"
    call = trace.snapshot(1)[0]
    assert call.engine == trace.OPENAI
    assert call.outcome == trace.MODEL
    assert call.is_live is True
    assert call.model == "gpt-4o-mini"


def test_a_failed_call_is_recorded_as_a_fallback_not_a_model_call():
    """The case the returned text cannot distinguish."""
    template = "Thinking about {competency}?"
    text = _openai_client(_Boom()).probe(template, "Delivery")

    # Grab the record before making any further call — snapshot() is newest-first.
    call = trace.snapshot(1)[0]

    assert text == template.format(competency="Delivery"), "behaviour must be unchanged"
    assert text == llm.StubLLM().probe(template, "Delivery"), "identical to stub output"

    assert call.outcome == trace.FALLBACK
    assert call.is_live is False
    assert call.is_problem is True
    assert call.detail == "RuntimeError"


def test_a_short_answer_never_reaches_the_model_and_says_so():
    fake = _Answers()
    _openai_client(fake).judge_specificity("Tell me more?", "yes")

    assert fake.calls == 0, "the deterministic filter should answer this alone"
    call = trace.snapshot(1)[0]
    assert call.engine == trace.OPENAI
    assert call.outcome == trace.HEURISTIC
    assert "too short" in call.detail


def test_themes_are_recorded_as_the_lexicon_even_in_live_mode():
    """Live mode never clusters with the model — the record must not imply it did."""
    _openai_client(_Answers()).cluster_themes([("b1", "they help colleagues readily")])
    call = trace.snapshot(1)[0]
    assert call.outcome == trace.LEXICON
    assert call.is_live is False


def test_no_answer_text_is_ever_recorded():
    """This is observability, not an audit trail — it must not become one.

    A record holding rater prose would be a second copy of feedback outside the
    redaction path, in memory, keyed to nothing.
    """
    secret = "Ana Torres missed the deadline and I had to chase her"
    llm.StubLLM().judge_specificity("What happened?", secret)

    for call in trace.snapshot():
        blob = " ".join(str(v) for v in vars(call).values())
        assert "Ana" not in blob and "deadline" not in blob and "chase" not in blob


def test_since_returns_only_this_turns_calls():
    llm.StubLLM().probe("a {competency}", "C1")
    mark = trace.latest_seq()
    llm.StubLLM().probe("b {competency}", "C2")
    llm.StubLLM().probe("c {competency}", "C3")

    fresh = trace.since(mark)
    assert [c.subject for c in fresh] == ["C2", "C3"]


def test_the_buffer_is_bounded():
    for i in range(trace.MAX_CALLS + 25):
        llm.StubLLM().followup("t")
    assert len(trace.snapshot(trace.MAX_CALLS * 2)) == trace.MAX_CALLS


def test_a_fallback_logs_at_warning(caplog):
    with caplog.at_level(logging.INFO, logger="poc.llm"):
        _openai_client(_Boom()).probe("{competency}?", "Delivery")
    record = caplog.records[-1]
    assert record.levelno == logging.WARNING
    assert "fallback" in record.getMessage()


def test_configure_logging_does_not_stack_handlers():
    before = list(trace.logger.handlers)
    trace.configure_logging()
    trace.configure_logging()
    try:
        assert len(trace.logger.handlers) <= max(1, len(before))
    finally:
        trace.logger.handlers = before
