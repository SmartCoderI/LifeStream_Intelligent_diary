import { AlertCircle, Ban, CheckCircle2, Clock, Loader2 } from "lucide-react";
import type { VideoItem } from "../../lib/api";

export default function StatusBadge({ video }: { video: Pick<VideoItem, "status" | "step_label"> }) {
  switch (video.status) {
    case "done":
      return <span className="badge badge-done"><CheckCircle2 size={15} /> Completed</span>;
    case "failed":
      return <span className="badge badge-failed"><AlertCircle size={15} /> Failed</span>;
    case "cancelled":
      return <span className="badge badge-cancelled"><Ban size={15} /> Cancelled</span>;
    case "queued":
      return <span className="badge badge-queued"><Clock size={15} /> Queued</span>;
    case "processing":
      return <span className="badge badge-processing"><Loader2 size={15} className="spin" /> Processing…</span>;
    default:
      return <span className="badge">Unknown</span>;
  }
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
    </div>
  );
}
