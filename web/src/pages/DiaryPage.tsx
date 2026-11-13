import { Film } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, useNavigate, useParams } from "react-router-dom";
import { api, type DayDetail, type DayItem } from "../lib/api";
import SummaryTab from "../components/video/SummaryTab";
import { clock, longDate } from "../lib/format";

/** One day's diary: every video recorded that day, each summary in its type's format
 *  (meeting: topics with speaker + timestamp per point; vlog: activities). Timestamps open the
 *  video at that moment. */
export default function DiaryPage() {
  const { date } = useParams();
  const navigate = useNavigate();
  const [days, setDays] = useState<DayItem[] | null>(null);
  const [day, setDay] = useState<DayDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listDays().then(setDays).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!date) return;
    setDay(null);
    setError(null);
    api.getDay(date).then(setDay).catch((e) => setError(e.message));
  }, [date]);

  if (!date && days && days.length > 0) return <Navigate to={`/diary/${days[0].date}`} replace />;

  return (
    <>
      <header className="page-header">
        <h1>Diary</h1>
        <p>Pick a day to read its summary, combining every video recorded that day.</p>
      </header>
      {error && <div className="card empty error-text" role="alert">Couldn't load the diary: {error}</div>}
      {!error && days === null && <div className="card empty">Loading…</div>}
      {days && days.length === 0 && <div className="card empty">No diary entries yet. Upload a video first.</div>}
      {days && days.length > 0 && (
        <div className="journal-layout">
          <nav className="card day-list" aria-label="Days">
            {days.map((d) => (
              <NavLink key={d.date} to={`/diary/${d.date}`}
                       className={({ isActive }) => `day-link${isActive ? " active" : ""}`}>
                <span>{longDate(d.date)}</span>
                <span className="muted">{d.video_count}</span>
              </NavLink>
            ))}
          </nav>
          <div>
            {!day && !error && <div className="card empty">Loading…</div>}
            {day && (
              <>
                <div className="card day-heading">
                  <h2>{longDate(day.date)}</h2>
                  <p className="muted">{day.videos.length} video{day.videos.length === 1 ? "" : "s"} recorded</p>
                </div>
                {day.videos.map((v) => (
                  <section className="card day-video" key={v.video_id} aria-label={v.title}>
                    <Link to={`/videos/${v.video_id}`} className="thumb day-thumb">
                      {v.thumbnail ? <img src={v.thumbnail} alt="" /> : <Film size={28} />}
                      {v.duration != null && <span className="duration">{clock(v.duration)}</span>}
                    </Link>
                    <SummaryTab summary={v.summary} colors={{}}
                                onSeek={(s) => navigate(`/videos/${v.video_id}?t=${Math.floor(s)}`)} />
                  </section>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
