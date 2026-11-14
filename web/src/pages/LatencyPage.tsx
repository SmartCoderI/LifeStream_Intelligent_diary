import { AlertCircle, CheckCircle2, ExternalLink, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Environment, type LatencySummary, type QueryRecord, type RunRecord, type VideoItem } from "../lib/api";
import { configLine, parallelNote } from "../lib/categories";
import { clockTime, duration, timeAgo } from "../lib/format";
import { usePolling } from "../lib/usePolling";
import BreakdownBar from "../components/latency/BreakdownBar";
import StepsToggle from "../components/latency/StepsToggle";
import Waterfall from "../components/latency/Waterfall";

interface Data { summary: LatencySummary; runs: RunRecord[]; queries: QueryRecord[]; active: VideoItem[] }

async function load(environment: Environment | null): Promise<Data> {
  const [summary, runs, queries, videos] = await Promise.all([
    api.latencySummary(20, environment), api.latencyRuns(50), api.latencyQueries(20), api.listVideos()]);
  return { summary, runs, queries, active: videos.filter((v) => v.status === "queued" || v.status === "processing") };
}

const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);
const ENV_LABELS: Record<Environment, string> = { cloud: "Cloud GPU", local: "This computer" };
const spanTime = (q: QueryRecord, category: string) =>
  q.stats.breakdown.filter((b) => b.category === category).reduce((t, b) => t + b.seconds, 0);

/** The newest cloud run of a ~1-hour video against the Stage 3 time budget. Its lines group
 *  the same timeline differently from the bar: model loading counts toward its step. */
function BudgetTable({ s }: { s: LatencySummary }) {
  if (!s.budget_lines) return null;
  const latest = s.budget;
  return (
    <section className="card overview budget">
      <h3 className="subhead">Time budget (1-hour video)</h3>
      <div className="table-wrap">
        <table className="latency-table">
          <thead><tr>
            <th>Budget line</th><th className="num">Target</th><th className="num">Latest 1-hour run</th>
          </tr></thead>
          <tbody>
            {s.budget_lines.map((b) => {
              const r = latest?.rows.find((x) => x.line === b.line);
              return (
                <tr key={b.line}>
                  <td>{b.label}</td>
                  <td className="num">{b.target}</td>
                  <td className={`num ${r ? (r.within ? "ok" : "over") : ""}`}>
                    {r ? `${duration(r.seconds)} ${r.within ? "✓" : "over"}` : "–"}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot><tr>
            <td>Upload → done</td><td className="num">&lt; 20 min</td>
            <td className={`num ${latest ? (latest.passed ? "ok" : "over") : ""}`}>
              {latest ? `${duration(latest.total)} ${latest.passed ? "✓ pass" : "fail"}` : "–"}
            </td>
          </tr></tfoot>
        </table>
      </div>
      <p className="muted small">Model loading counts toward its step here, as in the budget.
        {!latest && " No 1-hour video processed in the cloud yet. Upload a ~1-hour video (M3a) for the check."}</p>
    </section>
  );
}

function Status({ status }: { status: string }) {
  return status === "done"
    ? <CheckCircle2 size={14} className="ok-icon" aria-label="Done" />
    : <AlertCircle size={14} className="error-text" aria-label="Failed" />;
}

export default function LatencyPage() {
  const [environment, setEnvironment] = useState<Environment | null>(null);  // null: the server's default
  const { data, error, refresh } = usePolling(() => load(environment), () => true, 5000);
  useEffect(() => { if (environment) refresh(); }, [environment, refresh]);  // polling covers the first load
  const [runId, setRunId] = useState<string | null>(null);
  const [queryAt, setQueryAt] = useState<number | null>(null);

  if (error && !data) return <div className="card empty error-text" role="alert">{error}</div>;
  if (!data) return <div className="card empty">Loading…</div>;
  const { summary: s, queries } = data;
  const env = s.environment;
  const cloud = env === "cloud";
  // runs on the cloud GPU and on this computer aren't comparable: show one environment at a time
  const runs = env ? data.runs.filter((r) => (r.stats.environment ?? "local") === env).slice(0, 20) : data.runs;
  const both = (s.environments?.cloud ?? 0) > 0 && (s.environments?.local ?? 0) > 0;
  const run = runs.find((r) => r.run_id === runId) ?? runs[0];
  const query = queries.find((q) => q.at === queryAt) ?? queries[0];

  return (
    <>
      <header className="page-header latency-header">
        <div>
          <h1>Latency</h1>
          <p>Where processing time goes, so you can see what to fix first.</p>
        </div>
        {both && env && (
          <div className="muted context">
            <div className="env-switch" role="group" aria-label="Where videos were processed">
              {(["cloud", "local"] as Environment[]).map((e) => (
                <button key={e} aria-pressed={env === e} onClick={() => setEnvironment(e)}>
                  {ENV_LABELS[e]} ({s.environments![e]})
                </button>
              ))}
            </div>
          </div>
        )}
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

      {cloud && <BudgetTable s={s} />}

      <section className="latency-section">
        <h2>Runs</h2>
        {runs.length === 0 ? <div className="card empty">No runs yet. Upload a video to record one.</div> : (
          <div className="card">
            <div className="table-wrap">
              <table className="latency-table">
                <thead><tr>
                  <th>When</th><th>Video</th><th className="num">Length</th><th className="num">Total</th>
                  {cloud && <th className="num">Cold start</th>}
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
                      {cloud && (
                        <td className="num">
                          {r.stats.cold_start != null ? duration(r.stats.cold_start) : "–"}
                          {r.stats.cold != null && <span className="tag">{r.stats.cold ? "cold" : "warm"}</span>}
                        </td>
                      )}
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
                <p className="muted small">Wall-clock time. Steps run one after another, except scene detection, which runs
                  alongside transcription and speaker detection and only counts when nothing else was running.</p>
                {parallelNote(run.stats.parallel) && <p className="muted small">{parallelNote(run.stats.parallel)}</p>}
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
