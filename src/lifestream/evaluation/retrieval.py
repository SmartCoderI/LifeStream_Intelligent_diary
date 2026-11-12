"""F9 data test and M2 check, on generated videos with known answers.

"Today" is fixed at Wednesday 2026-11-11, so "last week" is 2026-11-02..08.

M2: "What did we discuss about the frontend last week?" must return the
frontend topic paragraph of `frontend-sync` (last week), not the near-identical
`frontend-kickoff` from three weeks earlier, and not other last-week meetings.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..memory.query import parse_query
from ..memory.store import MemoryStore
from ..schemas import (
    Activity, Classification, Location, MeetingActionItem, MeetingPoint, MeetingSummary, MeetingTopic,
    ScriptSegment, VideoSummary, VlogSummary,
)

TODAY = dt.date(2026, 11, 11)
M2_QUESTION = "What did we discuss about the frontend last week?"
M2_TARGET = ("frontend-sync", "Topic: Frontend")


def _meeting(video_id: str, date: str, title: str, overview: str,
             topics: list[tuple[str, list[tuple[str, str, str]]]],
             decisions: list[str], actions: list[tuple[str, str, str]], duration: float = 1800) -> VideoSummary:
    return VideoSummary(
        video_id=video_id, date=date, duration=duration,
        classification=Classification(type="meeting", confidence="high", reason="generated"),
        meeting=MeetingSummary(
            title=title, overview=overview, location=Location(value="Office", source="inferred"),
            attendees=sorted({p[0] for _, pts in topics for p in pts}),
            topics=[MeetingTopic(title=t, points=[MeetingPoint(speaker=s, timestamp=ts, content=c)
                                                  for s, ts, c in pts]) for t, pts in topics],
            decisions=decisions,
            action_items=[MeetingActionItem(owner=o, task=t, timestamp=ts) for o, t, ts in actions]))


def _script(lines: list[tuple[float, str, str]]) -> list[ScriptSegment]:
    return [ScriptSegment(start=t, end=t + 20, speaker=s, text=x) for t, s, x in lines]


def scenario() -> list[tuple[VideoSummary, list[ScriptSegment]]]:
    videos = [
        (_meeting(
            "frontend-sync", "2026-11-05", "Frontend weekly sync",
            "The team reviewed the frontend redesign and a slow dashboard, and agreed on fixes before Friday's demo.",
            [("Frontend: navigation bar and mobile layout", [
                ("Maya", "00:01:10", "The new React navigation bar is done but breaks on screens below 400 pixels."),
                ("Leo", "00:02:30", "The logo has a hard-coded width; switching it to a flexible CSS width fixes the layout."),
                ("Maya", "00:04:05", "Dark mode colors from design need a contrast check on the frontend."),
            ]),
             ("Dashboard chart loading", [
                ("Ana", "00:07:00", "Charts take four seconds to load because of the charting bundle."),
                ("Leo", "00:08:20", "Lazy loading the chart components should cut first load to one second."),
            ])],
            ["Ship the navigation bar fix before Friday's marketing demo."],
            [("Leo", "Fix the logo width in the navigation bar", "00:02:50"),
             ("Ana", "Lazy load dashboard charts and measure again", "00:08:40")]),
         _script([(70, "Maya", "the navigation bar breaks on small phones"),
                  (420, "Ana", "the charts are slow, four seconds on first load")])),
        (_meeting(
            "frontend-kickoff", "2026-10-21", "Frontend redesign kickoff",
            "Kickoff for the frontend redesign: scope, component library and timeline.",
            [("Frontend: redesign scope and component library", [
                ("Maya", "00:01:00", "We will rebuild the navigation and layout in React with a shared component library."),
                ("Leo", "00:03:00", "Mobile layout and dark mode are in scope for the frontend redesign."),
            ])],
            ["Use the shared component library for all new frontend screens."],
            [("Maya", "Draft the frontend redesign timeline", "00:05:00")]),
         _script([(60, "Maya", "kickoff for the frontend redesign")])),
        (_meeting(
            "backend-standup", "2026-11-04", "Backend standup",
            "Backend status: database migration progress and API rate limits.",
            [("Database migration", [
                ("Sam", "00:00:40", "The Postgres 17 migration finished on staging; production is planned for Sunday night."),
            ]),
             ("API rate limits", [
                ("Ivy", "00:03:10", "Public API rate limit will be 100 requests per minute per key, 1000 for partners."),
            ])],
            ["Migrate the production database on Sunday night."],
            [("Sam", "Write the rollback plan for the database migration", "00:01:30")]),
         _script([(40, "Sam", "staging migration is done")])),
        (_meeting(
            "marketing-plan", "2026-11-06", "Launch campaign planning",
            "Marketing planned the product launch campaign and its budget.",
            [("Launch campaign", [
                ("Zoe", "00:02:00", "The launch date is December 1st, with a webinar and an email campaign."),
                ("Ken", "00:04:00", "Ad budget for the launch is 20,000 dollars."),
            ])],
            ["Launch on December 1st."],
            [("Zoe", "Book the webinar platform", "00:02:30")]),
         _script([(120, "Zoe", "we launch on December first")])),
        (_meeting(
            "budget-review", "2026-11-10", "Q4 budget review",
            "Finance reviewed the Q4 budget.",
            [("Q4 budget", [
                ("Raj", "00:01:00", "The Q4 budget is 450,000 dollars, 10 percent over plan because of cloud costs."),
            ])],
            ["Cut cloud spend by 5 percent in December."],
            [("Raj", "Send the revised budget to leadership", "00:06:00")]),
         _script([(60, "Raj", "we are ten percent over plan")])),
        (VideoSummary(
            video_id="hike-vlog", date="2026-11-07", duration=900,
            classification=Classification(type="vlog", confidence="high", reason="generated"),
            vlog=VlogSummary(
                title="Saturday hike", overview="A Saturday hike up Mission Peak with views of the bay.",
                location=Location(value="Mission Peak, Fremont", source="inferred"),
                activities=[Activity(time="00:00:30", description="Starts the hike at the Stanford Avenue trailhead."),
                            Activity(time="00:09:00", description="Reaches the summit and films the view of the bay.")],
                action_items=[])),
         _script([(30, "SPEAKER_00", "starting the hike at the trailhead")])),
    ]
    return videos


@dataclass
class Question:
    text: str
    expected: list[tuple[str, str]]  # (video_id, section prefix); any counts as relevant


QUESTIONS = [
    Question(M2_QUESTION, [M2_TARGET]),
    Question("Why was the mobile navigation broken?", [("frontend-sync", "Topic: Frontend")]),
    Question("Who is fixing the logo width?", [("frontend-sync", "Action Items"), ("frontend-sync", "Topic: Frontend")]),
    Question("How slow were the dashboard charts?", [("frontend-sync", "Topic: Dashboard"), ("frontend-sync", "Transcript")]),
    Question("What did we decide at the frontend kickoff in October?",
             [("frontend-kickoff", "Key Decisions"), ("frontend-kickoff", "Topic"), ("frontend-kickoff", "Summary")]),
    Question("When is the production database migration?", [("backend-standup", "Topic: Database"), ("backend-standup", "Key Decisions")]),
    Question("What are the API rate limits?", [("backend-standup", "Topic: API")]),
    Question("What is the launch date?", [("marketing-plan", "Topic: Launch"), ("marketing-plan", "Key Decisions")]),
    Question("How big is the Q4 budget?", [("budget-review", "Topic: Q4"), ("budget-review", "Summary")]),
    Question("Where did I go hiking?", [("hike-vlog", "Summary"), ("hike-vlog", "Activity")]),
]


@dataclass
class RetrievalReport:
    k: int
    recall_at_k: float
    mrr: float
    m2_passed: bool
    m2_top: str
    rows: list[dict] = field(default_factory=list)


def evaluate(store: MemoryStore, k: int = 3, today: dt.date = TODAY) -> RetrievalReport:
    """Index the scenario into `store` (which should be an empty eval schema) and score it."""
    store.reset()
    for summary, script in scenario():
        store.index_video(summary, script)

    rows, reciprocal = [], []
    m2_passed, m2_top = False, ""
    for q in QUESTIONS:
        parsed = parse_query(q.text, today)
        hits = store.search(parsed.text, k=10, date_from=parsed.date_from, date_to=parsed.date_to)
        rank = next((i for i, h in enumerate(hits, 1)
                     if any(h.video_id == v and h.section.startswith(sec) for v, sec in q.expected)), None)
        reciprocal.append(1 / rank if rank else 0.0)
        top = f"{hits[0].video_id} / {hits[0].section}" if hits else "(nothing)"
        rows.append({"question": q.text, "rank": rank, "top": top,
                     "range": f"{parsed.date_from}..{parsed.date_to}" if parsed.date_from else ""})
        if q.text == M2_QUESTION:
            m2_top = top
            m2_passed = bool(hits) and hits[0].video_id == M2_TARGET[0] and hits[0].section.startswith(M2_TARGET[1])
    hits_at_k = sum(1 for r in rows if r["rank"] and r["rank"] <= k)
    return RetrievalReport(k=k, recall_at_k=hits_at_k / len(rows), mrr=sum(reciprocal) / len(reciprocal),
                           m2_passed=m2_passed, m2_top=m2_top, rows=rows)
