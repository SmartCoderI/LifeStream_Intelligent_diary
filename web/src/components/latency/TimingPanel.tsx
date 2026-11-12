import { api, type Timeline } from "../../lib/api";
import { configLine } from "../../lib/categories";
import { duration } from "../../lib/format";
import { usePolling } from "../../lib/usePolling";
import BreakdownBar from "./BreakdownBar";
import Kpis from "./Kpis";
import Waterfall from "./Waterfall";

const live = (t: Timeline) => t.status === "running" || t.status === "queued";

/** One video's latest run: 3 numbers, where the time went, then the step timeline.
 *  Refreshes every 2 s while the video is processing. */
export default function TimingPanel({ videoId }: { videoId: string }) {
  const { data, error } = usePolling(() => api.getTimeline(videoId), live, 2000);
  if (error) return <p className="muted">{error}</p>;
  if (!data) return <p className="muted">Loading timing…</p>;
  const s = data.stats;
  return (
    <div className="timing-panel">
      {data.status === "failed" && <p className="error-text">This run failed: {data.error}</p>}
      {s && (
        <>
          <Kpis items={[
            { label: live(data) ? "Elapsed so far" : "Total", value: duration(s.total) },
            { label: "Speed", value: s.speed != null ? `${s.speed.toFixed(2)}×` : "–",
              note: data.video_duration ? `of a ${duration(data.video_duration)} video` : undefined },
            { label: "Bottleneck", value: s.bottleneck?.label ?? "–",
              note: s.bottleneck ? `${Math.round(s.bottleneck.share * 100)}% · ${duration(s.bottleneck.seconds)}` : undefined },
          ]} />
          <BreakdownBar items={s.breakdown} />
          {s.cached_steps.length > 0 && (
            <p className="muted small">Reused saved results, not timed: {s.cached_steps.join(", ")}</p>
          )}
        </>
      )}
      <h4 className="subhead">Steps {configLine(s?.config) && <span className="muted">· {configLine(s?.config)}</span>}</h4>
      <Waterfall spans={data.spans} title={`Timeline for ${videoId}`} />
    </div>
  );
}
