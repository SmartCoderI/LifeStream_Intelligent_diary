import { useState } from "react";
import type { BreakdownItem } from "../../lib/api";
import { CATEGORY_HELP, CATEGORY_ORDER } from "../../lib/categories";
import { duration } from "../../lib/format";

interface Props {
  items: BreakdownItem[];
  /** show "s per video minute" instead of seconds (cross-run view) */
  perMinute?: boolean;
  /** hide the per-category rows (compact) */
  compact?: boolean;
}

const pct = (share: number) => `${share >= 0.095 ? Math.round(share * 100) : (share * 100).toFixed(1)}%`;

/** 100% stacked bar of where the time went, plus a labelled row per category. */
export default function BreakdownBar({ items, perMinute = false, compact = false }: Props) {
  const [hover, setHover] = useState<{ item: BreakdownItem; x: number; y: number } | null>(null);
  const sorted = [...items].sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category));
  if (sorted.length === 0) return <p className="muted">No timing data yet.</p>;
  const ranked = [...sorted].sort((a, b) => b.seconds - a.seconds);

  return (
    <div className="breakdown">
      <div className="bd-bar" role="img" aria-label={ranked.map((i) => `${i.label} ${pct(i.share)}`).join(", ")}>
        {sorted.map((i) => (
          <span key={i.category} className={`bd-seg cat-${i.category}`} style={{ flexGrow: i.share }}
                onMouseMove={(e) => setHover({ item: i, x: e.clientX, y: e.clientY })}
                onMouseLeave={() => setHover(null)} />
        ))}
      </div>
      {!compact && (
        <table className="bd-table">
          <tbody>
            {ranked.map((i) => (
              <tr key={i.category} title={CATEGORY_HELP[i.category]}>
                <td><span className={`bd-swatch cat-${i.category}`} />{i.label}</td>
                <td className="num"><strong>{pct(i.share)}</strong></td>
                <td className="num muted">
                  {perMinute && i.per_video_minute != null ? `${duration(i.per_video_minute)} / video min` : duration(i.seconds)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {hover && (
        <div className="wf-tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }} role="tooltip">
          <div className="wf-tt-title">{hover.item.label} · {pct(hover.item.share)}</div>
          <div>{duration(hover.item.seconds)}{perMinute && hover.item.per_video_minute != null
            ? ` (${duration(hover.item.per_video_minute)} per video minute)` : ""}</div>
          <div className="muted">{CATEGORY_HELP[hover.item.category]}</div>
        </div>
      )}
    </div>
  );
}
