"""F9: turn a question's time phrase into a date range.

    "What did we discuss about the frontend last week?"
    -> search text "What did we discuss about the frontend?", 2026-11-02 .. 2026-11-08

Common phrases are handled by rules (predictable, testable). If the question
contains a time word the rules don't cover, an LLM fills in the range.
Weeks run Monday to Sunday. `today` is passed in so tests can fix it.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

from pydantic import BaseModel, Field

from ..intelligence.llm import LLMClient

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "a": 1, "an": 1, "couple of": 2, "few": 3}
TIME_WORDS = re.compile(
    r"\b(ago|since|before|after|earlier|recent(ly)?|weekend|yesterday|today|tonight|morning|"
    r"week|month|year|quarter|days?|" + "|".join(WEEKDAYS) + "|" +
    "|".join(m for m in MONTHS if len(m) > 3) + r")\b", re.I)


class ParsedQuery(BaseModel):
    text: str = Field(description="The question with the time phrase removed.")
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    time_phrase: str | None = None


class _LLMRange(BaseModel):
    has_time_range: bool
    date_from: str | None = Field(None, description="YYYY-MM-DD, inclusive")
    date_to: str | None = Field(None, description="YYYY-MM-DD, inclusive")


def _week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _month_range(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def _rules(q: str, today: dt.date) -> tuple[re.Match, dt.date, dt.date] | None:
    """Return (matched phrase, start, end) for the first recognised time phrase."""
    ql = q.lower()
    num = r"(\d+|one|two|three|four|five|six|seven|a|an|couple of|few)"

    def n(word: str) -> int:
        return int(word) if word.isdigit() else NUMBERS[word]

    patterns: list[tuple[str, callable]] = [
        (r"\btoday\b|\bthis morning\b|\btonight\b", lambda m: (today, today)),
        (r"\byesterday\b", lambda m: (today - dt.timedelta(1),) * 2),
        (r"\bthe day before yesterday\b", lambda m: (today - dt.timedelta(2),) * 2),
        (r"\bthis week\b", lambda m: (_week_start(today), today)),
        (r"\blast week\b|\bprevious week\b",
         lambda m: (_week_start(today) - dt.timedelta(7), _week_start(today) - dt.timedelta(1))),
        (r"\blast weekend\b|\bthe weekend\b|\bover the weekend\b",
         lambda m: (_week_start(today) - dt.timedelta(2), _week_start(today) - dt.timedelta(1))),
        (r"\bthis month\b", lambda m: (today.replace(day=1), today)),
        (r"\blast month\b|\bprevious month\b",
         lambda m: _month_range(*((today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)))),
        (r"\bthis year\b", lambda m: (dt.date(today.year, 1, 1), today)),
        (rf"\b(?:in the |over the )?(?:past|last) {num} days?\b",
         lambda m: (today - dt.timedelta(n(m.group(1)) - 1), today)),
        (rf"\b(?:in the |over the )?(?:past|last) {num} weeks?\b",
         lambda m: (today - dt.timedelta(7 * n(m.group(1)) - 1), today)),
        (rf"\b{num} days? ago\b", lambda m: (today - dt.timedelta(n(m.group(1))),) * 2),
        (rf"\b{num} weeks? ago\b",
         lambda m: (_week_start(today) - dt.timedelta(7 * n(m.group(1))),
                    _week_start(today) - dt.timedelta(7 * n(m.group(1)) - 6))),
        (r"\b(\d{4})-(\d{2})-(\d{2})\b",
         lambda m: (dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))),) * 2),
        (r"\b(?:on )?(" + "|".join(MONTHS) + r")\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?\b",
         lambda m: (_dated(m, today),) * 2),
        (r"\b(?:on )?(?:last )?(" + "|".join(WEEKDAYS) + r")\b",
         lambda m: (_last_weekday(m.group(1), today),) * 2),
        (r"\bin (" + "|".join(m for m in MONTHS if len(m) > 3) + r")(?: (\d{4}))?\b",
         lambda m: _month_range(int(m.group(2)) if m.group(2) else
                                (today.year if MONTHS[m.group(1)] <= today.month else today.year - 1),
                                MONTHS[m.group(1)])),
    ]
    for pattern, to_range in patterns:
        m = re.search(pattern, ql)
        if m:
            start, end = to_range(m)
            return m, start, end
    return None


def _dated(m: re.Match, today: dt.date) -> dt.date:
    month, day = MONTHS[m.group(1)], int(m.group(2))
    year = int(m.group(3)) if m.group(3) else today.year
    d = dt.date(year, month, day)
    if not m.group(3) and d > today:  # "Dec 20" asked in January means last December
        d = dt.date(year - 1, month, day)
    return d


def _last_weekday(name: str, today: dt.date) -> dt.date:
    """Most recent past occurrence ("Tuesday" asked on a Tuesday means a week ago)."""
    delta = (today.weekday() - WEEKDAYS.index(name)) % 7 or 7
    return today - dt.timedelta(delta)


def _strip(q: str, start: int, end: int) -> str:
    text = (q[:start] + q[end:]).strip()
    text = re.sub(r"\s+([?.!,])", r"\1", re.sub(r"\s{2,}", " ", text))
    return text or q


def parse_query(question: str, today: dt.date, llm: LLMClient | None = None) -> ParsedQuery:
    question = question.strip()
    found = _rules(question, today)
    if found:
        m, start, end = found
        if start > end:
            start, end = end, start
        return ParsedQuery(text=_strip(question, m.start(), m.end()), date_from=start, date_to=end,
                           time_phrase=question[m.start():m.end()])

    if llm is not None and TIME_WORDS.search(question):
        result = llm.parse(
            system=("Extract the date range a question refers to. Today is "
                    f"{today.isoformat()} ({WEEKDAYS[today.weekday()].title()}). Weeks run Monday "
                    "to Sunday. If the question has no time restriction, set has_time_range false."),
            content=[{"type": "text", "text": question}],
            schema=_LLMRange, max_tokens=2000, effort="low")
        if result.has_time_range:
            try:
                start = dt.date.fromisoformat(result.date_from) if result.date_from else None
                end = dt.date.fromisoformat(result.date_to) if result.date_to else None
                return ParsedQuery(text=question, date_from=start, date_to=end, time_phrase="(interpreted)")
            except ValueError:
                pass
    return ParsedQuery(text=question)
