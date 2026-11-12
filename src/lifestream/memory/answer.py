"""F9: answer a question from the indexed videos, with citations."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from ..intelligence.llm import LLMClient
from ..timing import fmt_ts
from .query import ParsedQuery, parse_query
from .store import MemoryStore, SearchHit

ANSWER_SYSTEM = """\
You answer questions about a person's recorded videos (meetings, vlogs) using \
only the numbered excerpts provided. Each excerpt shows the video title, date, \
section and timestamps.

- Answer directly and specifically, in a few sentences or a short list.
- Cite the excerpts you used by number in `citations`; cite only excerpts that \
support the answer.
- If the excerpts don't contain the answer, set found to false and say briefly \
what you couldn't find. Never use outside knowledge or guess."""


class _LLMAnswer(BaseModel):
    found: bool
    answer: str
    citations: list[int] = Field(description="Excerpt numbers that support the answer.")


class Citation(BaseModel):
    video_id: str
    title: str
    date: str
    kind: str
    section: str
    start: float
    end: float
    snippet: str


class Answer(BaseModel):
    question: str
    answer: str
    found: bool
    citations: list[Citation]
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    time_phrase: str | None = None


def _snippet(hit: SearchHit, limit: int = 280) -> str:
    body = hit.text.split("\n", 1)[1] if "\n" in hit.text else hit.text  # drop the header line
    return body if len(body) <= limit else body[:limit].rsplit(" ", 1)[0] + "…"


def _range_text(p: ParsedQuery) -> str:
    if p.date_from and p.date_to and p.date_from == p.date_to:
        return f"on {p.date_from.isoformat()}"
    return f"between {p.date_from} and {p.date_to}"


def answer_question(
    question: str,
    store: MemoryStore,
    llm: LLMClient,
    today: dt.date,
    k: int = 6,
) -> Answer:
    parsed = parse_query(question, today, llm)
    hits = store.search(parsed.text, k=k, date_from=parsed.date_from, date_to=parsed.date_to)
    base = dict(question=question, date_from=parsed.date_from, date_to=parsed.date_to,
                time_phrase=parsed.time_phrase)
    if not hits:
        where = f" {_range_text(parsed)}" if parsed.date_from or parsed.date_to else ""
        return Answer(answer=f"I couldn't find any videos{where} to answer that.", found=False,
                      citations=[], **base)

    excerpts = "\n\n".join(
        f"[{i}] {h.title} | {h.date} | {h.section} | {fmt_ts(h.start)}-{fmt_ts(h.end)}\n{_snippet(h, 2000)}"
        for i, h in enumerate(hits, 1))
    result = llm.parse(
        system=ANSWER_SYSTEM,
        content=[{"type": "text", "text": f"Excerpts:\n\n{excerpts}\n\nQuestion: {question}"}],
        schema=_LLMAnswer, max_tokens=4000, effort="low")

    cited = [hits[i - 1] for i in dict.fromkeys(result.citations) if 1 <= i <= len(hits)]
    if result.found and not cited:
        cited = hits[:1]
    return Answer(
        answer=result.answer, found=result.found,
        citations=[Citation(video_id=h.video_id, title=h.title, date=h.date, kind=h.kind, section=h.section,
                            start=h.start, end=h.end, snippet=_snippet(h)) for h in cited],
        **base)
