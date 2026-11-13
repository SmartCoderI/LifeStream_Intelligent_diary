import { useState } from "react";
import type { Category, Span } from "../../lib/api";
import { CATEGORY_ORDER } from "../../lib/categories";
import { clockTime, compactNumber, duration } from "../../lib/format";

const CATEGORY_LABELS: Record<Category, string> = {
  speech: "Speech models", llm: "LLM", model_loading: "Model loading", media: "Media (FFmpeg)",
  embedding: "Embedding", database: "Database", upload_queue: "Upload & queue", other: "Other",
};
const cat = (s: Span): Category => s.category ?? "other";

/** Parents first (by start time), each followed by its sub-steps. */
function ordered(spans: Span[]): { span: Span; child: boolean }[] {
  const byStart = [...spans].sort((a, b) => a.start - b.start);
  const top = byStart.filter((s) => !s.parent);
  const out: { span: Span; child: boolean }[] = [];
  for (const s of top) {
    out.push({ span: s, child: false });
    byStart.filter((c) => c.parent === s.name).forEach((c) => out.push({ span: c, child: true }));
  }
  // children whose parent isn't recorded (e.g. failed mid-step)
  byStart.filter((c) => c.parent && !top.some((t) => t.name === c.parent))
    .forEach((c) => out.push({ span: c, child: true }));
  return out;
}

export function metaText(span: Span): string {
  const m = span.meta ?? {};
  const parts: string[] = [];
  if (typeof m.input_tokens === "number") {
    parts.push(`${compactNumber(m.input_tokens)} in / ${compactNumber(Number(m.output_tokens ?? 0))} out tokens`);
  }
  for (const key of ["words", "speakers", "scenes", "keyframes", "turns", "chunks", "hits", "segments"]) {
    if (typeof m[key] === "number") parts.push(`${m[key]} ${key}`);
  }
  if (typeof m.bytes === "number") parts.push(`${(Number(m.bytes) / 1024 ** 2).toFixed(1)} MB`);
  if (typeof m.result === "string") parts.push(m.result);
  if (m.llm_used === true) parts.push("used the LLM");
  if (m.cold === true) parts.push("cold");
  if (m.cold === false) parts.push("warm");
  return parts.join(" · ");
}

interface Props {
  spans: Span[];
  /** caption for screen readers */
  title?: string;
}

export default function Waterfall({ spans, title = "Latency timeline" }: Props) {
  const [hover, setHover] = useState<{ span: Span; x: number; y: number } | null>(null);
  if (spans.length === 0) return <p className="muted">No timing recorded yet.</p>;

  const t0 = Math.min(...spans.map((s) => s.start));
  const t1 = Math.max(...spans.map((s) => s.end));
  const range = Math.max(t1 - t0, 0.001);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ f, label: duration(range * f) }));
  const rows = ordered(spans);
  const cats = CATEGORY_ORDER.filter((c) => spans.some((s) => s.kind !== "cached" && cat(s) === c));
  const hasCached = spans.some((s) => s.kind === "cached");
  const hasRunning = spans.some((s) => s.running);

  return (
    <figure className="waterfall" aria-label={title}>
      <div className="wf-legend" aria-label="Legend">
        {cats.map((c) => (
          <span key={c} className="wf-legend-item"><span className={`wf-swatch cat-${c}`} />{CATEGORY_LABELS[c]}</span>
        ))}
        {hasCached && <span className="wf-legend-item"><span className="wf-swatch wf-cached" />Reused (skipped)</span>}
        {hasRunning && <span className="wf-legend-item"><span className="wf-swatch wf-running" />In progress</span>}
      </div>

      <div className="wf-grid" role="table" aria-label={title}>
        <div className="wf-row wf-axis" role="row" aria-hidden="true">
          <span />
          <span className="wf-track">
            {ticks.map((t) => (
              <span key={t.f} className="wf-tick" style={{ left: `${t.f * 100}%` }}>{t.label}</span>
            ))}
          </span>
          <span />
        </div>
        {rows.map(({ span, child }, i) => {
          const left = ((span.start - t0) / range) * 100;
          const width = Math.max((span.duration / range) * 100, 0.4);
          const extra = metaText(span);
          return (
            <div key={`${span.name}-${i}`} className={`wf-row${child ? " wf-child" : ""}`} role="row"
                 data-testid="wf-row">
              <span className="wf-label" role="cell" title={span.label}>{span.label}</span>
              <span className="wf-track" role="cell">
                {[0.25, 0.5, 0.75].map((f) => <span key={f} className="wf-gridline" style={{ left: `${f * 100}%` }} />)}
                <span
                  className={`wf-bar ${span.kind === "cached" ? "wf-cached" : `cat-${cat(span)}`}${span.running ? " wf-running" : ""}`}
                  style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
                  onMouseMove={(e) => setHover({ span, x: e.clientX, y: e.clientY })}
                  onMouseLeave={() => setHover(null)}
                  aria-label={`${span.label}: ${duration(span.duration)}`}
                />
              </span>
              <span className="wf-value" role="cell">
                <strong>{span.kind === "cached" ? "reused" : duration(span.duration)}</strong>
                {span.running && <span className="muted"> so far</span>}
                {extra && <span className="wf-meta">{extra}</span>}
              </span>
            </div>
          );
        })}
      </div>

      {hover && (
        <div className="wf-tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }} role="tooltip">
          <div className="wf-tt-title">{hover.span.label}</div>
          <div>{hover.span.kind === "cached" ? "Reused (skipped)" : CATEGORY_LABELS[cat(hover.span)]}
            {hover.span.running ? " (still running)" : ""}</div>
          <div>Duration: <strong>{duration(hover.span.duration)}</strong></div>
          <div className="muted">Start {clockTime(hover.span.start)}</div>
          <div className="muted">End {clockTime(hover.span.end)}</div>
          <div className="muted">+{duration(hover.span.start - t0)} from first step</div>
          {metaText(hover.span) && <div>{metaText(hover.span)}</div>}
          {typeof hover.span.meta?.model === "string" && <div className="muted">{String(hover.span.meta.model)}</div>}
        </div>
      )}
    </figure>
  );
}
