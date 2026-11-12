"""F9: answers with citations (fake store + fake LLM)."""

import datetime as dt

from lifestream.memory.answer import _LLMAnswer, answer_question
from lifestream.memory.store import SearchHit

TODAY = dt.date(2026, 11, 11)


def hit(i, section, video="frontend-sync"):
    return SearchHit(chunk_id=i, video_id=video, title="Frontend sync", date="2026-11-05", kind="summary",
                     section=section, start=70.0 * i, end=70.0 * i + 60, text=f"header\n{section} text {i}",
                     score=0.8 - i / 10)


class FakeStore:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def search(self, text, k, date_from=None, date_to=None, timings=None):
        self.calls.append((text, date_from, date_to))
        return self.hits


class FakeAnswerLLM:
    def __init__(self, out):
        self.out, self.prompts = out, []

    def parse(self, *, system, content, schema, max_tokens, effort):
        if schema is _LLMAnswer:
            self.prompts.append(content[0]["text"])
            return self.out
        raise AssertionError("no LLM date parsing expected")


def test_answer_with_citations_and_date_filter():
    store = FakeStore([hit(1, "Topic: Frontend"), hit(2, "Action Items")])
    llm = FakeAnswerLLM(_LLMAnswer(found=True, answer="The nav bar breaks below 400px.", citations=[1, 1, 9]))
    a = answer_question("What did we discuss about the frontend last week?", store, llm, TODAY)
    assert store.calls == [("What did we discuss about the frontend?", dt.date(2026, 11, 2), dt.date(2026, 11, 8))]
    assert a.found and a.answer.startswith("The nav bar")
    assert [c.section for c in a.citations] == ["Topic: Frontend"]  # duplicates and bad numbers dropped
    assert a.citations[0].snippet == "Topic: Frontend text 1"  # header line removed
    assert "[1] Frontend sync | 2026-11-05 | Topic: Frontend | 00:01:10-00:02:10" in llm.prompts[0]


def test_nothing_in_range_skips_llm():
    llm = FakeAnswerLLM(None)
    a = answer_question("What happened yesterday?", FakeStore([]), llm, TODAY)
    assert not a.found and a.citations == [] and llm.prompts == []
    assert "on 2026-11-10" in a.answer


def test_not_found_answer_keeps_no_citations():
    llm = FakeAnswerLLM(_LLMAnswer(found=False, answer="No budget was discussed.", citations=[]))
    a = answer_question("What about the budget?", FakeStore([hit(1, "Summary")]), llm, TODAY)
    assert not a.found and a.citations == []
