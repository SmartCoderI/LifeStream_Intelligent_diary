"""F9: time phrases -> date ranges (today fixed to Wednesday 2026-11-11)."""

import datetime as dt

import pytest
from pydantic import BaseModel

from lifestream.memory.query import _LLMRange, parse_query

TODAY = dt.date(2026, 11, 11)
D = dt.date


@pytest.mark.parametrize("question, start, end", [
    ("What did we discuss about the frontend last week?", D(2026, 11, 2), D(2026, 11, 8)),
    ("what happened today", TODAY, TODAY),
    ("meetings yesterday", D(2026, 11, 10), D(2026, 11, 10)),
    ("anything this week?", D(2026, 11, 9), TODAY),
    ("budget talks last month", D(2026, 10, 1), D(2026, 10, 31)),
    ("in the past 3 days", D(2026, 11, 9), TODAY),
    ("over the last two weeks", D(2026, 10, 29), TODAY),
    ("two weeks ago", D(2026, 10, 26), D(2026, 11, 1)),
    ("on Nov 4", D(2026, 11, 4), D(2026, 11, 4)),
    ("on December 20th", D(2025, 12, 20), D(2025, 12, 20)),  # future date -> last year
    ("on 2026-10-21", D(2026, 10, 21), D(2026, 10, 21)),
    ("on Tuesday", D(2026, 11, 10), D(2026, 11, 10)),
    ("last Wednesday", D(2026, 11, 4), D(2026, 11, 4)),  # same weekday -> a week ago
    ("what did I do in October", D(2026, 10, 1), D(2026, 10, 31)),
    ("over the weekend", D(2026, 11, 7), D(2026, 11, 8)),
])
def test_time_phrases(question, start, end):
    p = parse_query(question, TODAY)
    assert (p.date_from, p.date_to) == (start, end)


def test_time_phrase_removed_from_search_text():
    p = parse_query("What did we discuss about the frontend last week?", TODAY)
    assert p.text == "What did we discuss about the frontend?"
    assert p.time_phrase == "last week"


def test_no_time_phrase():
    p = parse_query("Who is fixing the logo?", TODAY)
    assert (p.text, p.date_from, p.date_to) == ("Who is fixing the logo?", None, None)


class FakeRangeLLM:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def parse(self, *, system, content, schema, max_tokens, effort):
        assert schema is _LLMRange and "2026-11-11" in system
        self.calls += 1
        return self.result


def test_llm_fallback_for_unusual_phrases():
    llm = FakeRangeLLM(_LLMRange(has_time_range=True, date_from="2026-09-01", date_to="2026-09-30"))
    p = parse_query("what did we do back at the start of the quarter", TODAY, llm)
    assert (p.date_from, p.date_to, llm.calls) == (D(2026, 9, 1), D(2026, 9, 30), 1)


def test_llm_not_called_without_time_words():
    llm = FakeRangeLLM(_LLMRange(has_time_range=False))
    parse_query("who owns the logo fix", TODAY, llm)
    assert llm.calls == 0
