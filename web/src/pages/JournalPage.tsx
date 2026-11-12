import { Film } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, useParams } from "react-router-dom";
import { api, type DayDetail, type DayItem } from "../lib/api";
import { clock, longDate } from "../lib/format";

export default function JournalPage() {
  const { date } = useParams();
  const [days, setDays] = useState<DayItem[] | null>(null);
  const [day, setDay] = useState<DayDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listDays().then(setDays).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!date) return;
    setDay(null);
    api.getDay(date).then(setDay).catch((e) => setError(e.message));
  }, [date]);

  if (!date && days && days.length > 0) return <Navigate to={`/journal/${days[0].date}`} replace />;

  const actions = day?.videos.flatMap((v) => {
    const items = v.summary.meeting?.action_items ?? v.summary.vlog?.action_items ?? [];
    return items.map((a) => ({ task: a.task, owner: a.owner, video: v.title, id: v.video_id }));
  }) ?? [];

  return (
    <>
      <header className="page-header">
        <h1>Daily Journal</h1>
        <p>One entry per day, combining every video you recorded that day.</p>
      </header>
      {error && <div className="card empty error-text" role="alert">{error}</div>}
      {days && days.length === 0 && <div className="card empty">No journal entries yet. Process a video first.</div>}
      {days && days.length > 0 && (
        <div className="journal-layout">
          <nav className="card day-list" aria-label="Days">
            {days.map((d) => (
              <NavLink key={d.date} to={`/journal/${d.date}`} className={({ isActive }) => `day-link${isActive ? " active" : ""}`}>
                <span>{longDate(d.date)}</span>
                <span className="muted">{d.video_count}</span>
              </NavLink>
            ))}
          </nav>
          <div className="card">
            {!day && !error && <div className="empty">Loading…</div>}
            {day && (
              <>
                <div style={{ padding: "18px 18px 0" }}>
                  <h2>{longDate(day.date)}</h2>
                  <p className="muted" style={{ margin: "4px 0 0" }}>
                    {day.videos.length} video{day.videos.length === 1 ? "" : "s"} recorded
                  </p>
                </div>
                {day.videos.map((v) => {
                  const body = v.summary.meeting ?? v.summary.vlog;
                  return (
                    <div className="day-entry" key={v.video_id}>
                      <Link to={`/videos/${v.video_id}`} className="thumb" style={{ width: "100%" }}>
                        {v.thumbnail ? <img src={v.thumbnail} alt="" /> : <Film size={28} />}
                        {v.duration != null && <span className="duration">{clock(v.duration)}</span>}
                      </Link>
                      <div>
                        <h3><Link to={`/videos/${v.video_id}`}>{body?.title ?? v.title}</Link></h3>
                        <div className="summary-meta" style={{ margin: "0 0 8px" }}>
                          <span className="badge badge-type">{v.summary.classification.type}</span>
                          {body && body.location.source !== "unknown" && <span>{body.location.value}</span>}
                        </div>
                        <p style={{ margin: 0 }}>{body?.overview}</p>
                      </div>
                    </div>
                  );
                })}
                <div className="section" style={{ padding: "0 18px 18px" }}>
                  <div className="section-title">All Action Items</div>
                  {actions.length === 0 && <p className="muted" style={{ margin: 0 }}>None</p>}
                  <ul>
                    {actions.map((a, i) => (
                      <li key={i}>{a.task}{a.owner ? ` (${a.owner})` : ""} <span className="muted">· {a.video}</span></li>
                    ))}
                  </ul>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
