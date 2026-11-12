import { CalendarDays, Clock, MapPin, Users } from "lucide-react";
import { useState } from "react";
import type { VideoSummary } from "../../lib/api";
import { clock, longDate, parseTimestamp } from "../../lib/format";

interface Props {
  summary: VideoSummary;
  colors: Record<string, string>;
  onSeek: (seconds: number) => void;
}

export function Timestamp({ value, onSeek }: { value: string; onSeek: (s: number) => void }) {
  const seconds = parseTimestamp(value);
  if (seconds === null) return <span className="muted">{value}</span>;
  return (
    <button className="ts-btn" onClick={() => onSeek(seconds)} title="Play from here">{clock(seconds)}</button>
  );
}

export function SpeakerChip({ speaker, colors }: { speaker: string; colors: Record<string, string> }) {
  const label = speaker.split(" ")[0];
  return <span className="speaker-chip" style={{ background: colors[label] ?? "#6b6880" }}>{speaker}</span>;
}

function ActionItem({ task, meta }: { task: string; meta: React.ReactNode }) {
  const [done, setDone] = useState(false);
  return (
    <label className={`action${done ? " checked" : ""}`}>
      <input type="checkbox" checked={done} onChange={(e) => setDone(e.target.checked)} />
      <span>
        <span className="task">{task}</span>
        {meta && <div className="meta">{meta}</div>}
      </span>
    </label>
  );
}

function Location({ value, source }: { value: string; source: string }) {
  return (
    <span><MapPin size={14} /> {value}{source === "inferred" ? " (inferred)" : ""}</span>
  );
}

export default function SummaryTab({ summary, colors, onSeek }: Props) {
  const m = summary.meeting;
  const v = summary.vlog;
  const body = m ?? v;
  if (!body) return <p className="muted">No summary.</p>;

  return (
    <article className="summary-doc">
      <h2>{body.title}</h2>
      <div className="summary-meta">
        <span className="badge badge-type">{summary.classification.type}</span>
        <span><CalendarDays size={14} /> {longDate(summary.date)}</span>
        <span><Clock size={14} /> {clock(summary.duration)}</span>
        {m && <span><Users size={14} /> {m.attendees.length} speakers</span>}
        {body.location.source !== "unknown" && <Location {...body.location} />}
      </div>

      <section className="section">
        <div className="section-title">Summary</div>
        <p style={{ margin: 0 }}>{body.overview || <span className="muted">No overview for this video.</span>}</p>
      </section>

      {m && (m.decisions ?? []).length > 0 && (
        <section className="section">
          <div className="section-title">Key Decisions</div>
          <ul>{m.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul>
        </section>
      )}

      {m && (
        <section className="section">
          <div className="section-title">Discussion</div>
          {m.topics.map((t, i) => (
            <div className="topic" key={i}>
              <h4>{t.title}</h4>
              {t.points.map((p, j) => (
                <div className="point" key={j}>
                  <Timestamp value={p.timestamp} onSeek={onSeek} />
                  <div><SpeakerChip speaker={p.speaker} colors={colors} />{p.content}</div>
                </div>
              ))}
            </div>
          ))}
        </section>
      )}

      {v && (
        <section className="section">
          <div className="section-title">Activity</div>
          {v.activities.map((a, i) => (
            <div className="point" key={i}>
              <Timestamp value={a.time} onSeek={onSeek} />
              <div>{a.description}</div>
            </div>
          ))}
        </section>
      )}

      <section className="section">
        <div className="section-title">Action Items</div>
        {body.action_items.length === 0 && <p className="muted" style={{ margin: 0 }}>None</p>}
        {m?.action_items.map((a, i) => (
          <ActionItem key={i} task={a.task} meta={
            <>{a.owner}{a.due ? ` · due ${a.due}` : ""} · <Timestamp value={a.timestamp} onSeek={onSeek} /></>
          } />
        ))}
        {v?.action_items.map((a, i) => <ActionItem key={i} task={a.task} meta={a.owner} />)}
      </section>
    </article>
  );
}
