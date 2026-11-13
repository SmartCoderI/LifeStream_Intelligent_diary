import { AlertCircle, CheckCircle2, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type LatencySummary, type QueryRecord, type RunRecord, type VideoItem } from "../lib/api";
import { configLine } from "../lib/categories";
import { clockTime, duration, timeAgo } from "../lib/format";
import { usePolling } from "../lib/usePolling";
import BreakdownBar from "../components/latency/BreakdownBar";
import Kpis from "../components/latency/Kpis";
import StepsToggle from "../components/latency/StepsToggle";
import Waterfall from "../components/latency/Waterfall";

interface Data { summary: LatencySummary; runs: RunRecord[]; queries: QueryRecord[]; active: VideoItem[] }

async function load(): Promise<Data> {
  const [summary, runs, queries, videos] = await Promise.all([
    api.latencySummary(), api.latencyRuns(20), api.latencyQueries(20), api.listVideos()]);
  return { summary, runs, queries, active: videos.filter((v) => v.status === "queued" || v.status === "processing") };
}

const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const spanTime = (q: QueryRecord, category: string) =>
  q.stats.breakdown.filter((b) => b.category === category).reduce((t, b) => t + b.seconds, 0);

function Status({ status }: { status: string }) {
  return status === "done"
    ? <CheckCircle2 size={14} className="ok-icon" aria-label="Done" />
    : <AlertCircle size={14} className="error-text" aria-label="Failed" />;
}

export default function LatencyPage() {
  const { data, error } = usePolling(load, () => true, 5000);
  const [runId, setRunId] = useState<string | null>(null);
  const [queryAt, setQueryAt] = useState<number | null>(null);

  if (error && !data) return <div className="card empty error-text" role="alert">{error}</div>;
  if (!data) return <div className="card empty">Loading…</div>;
  const { summary: s, runs, queries } = data;
  const run = runs.find((r) => r.run_id === runId) ?? runs[0];
  const query = queries.find((q) => q.at === queryAt) ?? queries[0];
  const q = s.queries;

  return (
    <>
      <header className="page-header latency-header">
        <div>
          <h1>Latency</h1>
          <p>Where processing time goes, so you can see what to fix first.</p>
        </div>
        <div className="muted context">
          {s.runs ? `Last ${s.runs} full run${s.runs === 1 ? "" : "s"}` : "No full runs yet"}
          {configLine(s.config) && ` · ${configLine(s.config)}`}
        </div>
      </header>

      {data.active.length > 0 && (
        <div className="card active-runs">
          <Loader2 size={15} className="spin" /> Processing now:
          {data.active.map((v) => (
            <Link key={v.video_id} to={`/videos/${v.video_id}`} className="chip">
              {v.title} · {v.step_label} <ExternalLink size={12} />
            </Link>
          ))}
        </div>
      )}

      <section className="card overview">
        <Kpis items={[
          { label: "Processing speed", value: s.speed_median != null ? `${s.speed_median.toFixed(2)}×` : "–",
            note: "time per second of video (median)" },
          { label: "Bottleneck", value: s.bottleneck?.label ?? "–",
            note: s.bottleneck ? `${pct(s.bottleneck.share)} of processing time` : undefined, tone: "warn" },
          { label: "LLM share", value: pct(s.llm_share), note: "of processing time" },
          { label: "Ask / Search", value: q.p50 != null ? duration(q.p50) : "–",
            note: q.count ? `p50 · p95 ${duration(q.p95)} · ${q.count} queries` : "no queries yet" },
        ]} />
        <h3 className="subhead">Where the time goes</h3>
        <BreakdownBar items={s.breakdown} perMinute />
        {s.runs_skipped > 0 && (
          <p className="muted small">{s.runs_skipped} run{s.runs_skipped === 1 ? "" : "s"} left out: failed, reused saved
            results (steps not re-timed), or recorded before the detailed breakdown.</p>
        )}
      </section>

      <section className="latency-section">
        <h2>Runs</h2>
        {runs.length === 0 ? <div className="card empty">No runs yet. Upload a video to record one.</div> : (
          <div className="card">
            <div className="table-wrap">
              <table className="latency-table">
                <thead><tr>
                  <th>When</th><th>Video</th><th className="num">Length</th><th className="num">Total</th>
                  <th className="num">Speed</th><th>Bottleneck</th>
                </tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.run_id} className={r.run_id === run?.run_id ? "selected" : ""} tabIndex={0}
                        onClick={() => setRunId(r.run_id)} onKeyDown={(e) => e.key === "Enter" && setRunId(r.run_id)}>
                      <td title={clockTime(r.started_at)}><Status status={r.status} /> {timeAgo(r.started_at)}</td>
                      <td>{r.title}{r.deleted && <span className="muted"> (deleted)</span>}
                        {r.stats.cached_steps.length > 0 && <span className="tag">reused</span>}</td>
                      <td className="num">{duration(r.video_duration)}</td>
                      <td className="num"><strong>{duration(r.stats.total)}</strong></td>
                      <td className="num">{r.stats.speed != null ? `${r.stats.speed.toFixed(2)}×` : "–"}</td>
                      <td>{r.stats.bottleneck ? `${r.stats.bottleneck.label} · ${pct(r.stats.bottleneck.share)}` : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {run && (
              <div className="detail">
                <div className="detail-head">
                  <strong>{run.title}</strong>
                  <span className="muted">{clockTime(run.started_at)} · {configLine(run.stats.config)}</span>
                  {!run.deleted && <Link to={`/videos/${run.video_id}`} className="detail-link">Open video <ExternalLink size={13} /></Link>}
                </div>
                {run.status === "failed" && <p className="error-text">Failed: {run.error}</p>}
                <BreakdownBar items={run.stats.breakdown} />
                <StepsToggle><Waterfall spans={run.spans} title={`Timeline of ${run.title}`} /></StepsToggle>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="latency-section">
        <h2>Ask / Search</h2>
        {queries.length === 0 ? <div className="card empty">No questions asked yet.</div> : (
          <div className="card">
            <div className="table-wrap">
              <table className="latency-table">
                <thead><tr>
                  <th>When</th><th>Question</th><th className="num">Total</th><th className="num">LLM</th>
                  <th className="num">Database</th><th className="num">Embedding</th>
                </tr></thead>
                <tbody>
                  {queries.map((r) => (
                    <tr key={r.at} className={r.at === query?.at ? "selected" : ""} tabIndex={0}
                        onClick={() => setQueryAt(r.at)} onKeyDown={(e) => e.key === "Enter" && setQueryAt(r.at)}>
                      <td title={clockTime(r.at)}><Status status={r.status} /> {timeAgo(r.at)}</td>
                      <td className="question-cell">{r.question}</td>
                      <td className="num"><strong>{duration(r.total)}</strong></td>
                      <td className="num">{duration(spanTime(r, "llm"))}</td>
                      <td className="num">{duration(spanTime(r, "database"))}</td>
                      <td className="num">{duration(spanTime(r, "embedding"))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {query && (
              <div className="detail">
                <div className="detail-head"><strong>“{query.question}”</strong></div>
                {query.status === "failed" && <p className="error-text">Failed: {query.error}</p>}
                <BreakdownBar items={query.stats.breakdown} />
                <StepsToggle><Waterfall spans={query.spans} title="Query timeline" /></StepsToggle>
              </div>
            )}
          </div>
        )}
      </section>
    </>
  );
}
