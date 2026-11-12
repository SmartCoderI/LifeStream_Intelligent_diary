import { BookOpen, Film, RotateCw, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { VideoItem } from "../api";
import { clock, fileSize, timeAgo } from "../format";
import StatusBadge, { ProgressBar } from "./StatusBadge";

interface Props {
  videos: VideoItem[];
  onDelete: (v: VideoItem) => void;
  onRetry: (v: VideoItem) => void;
}

export default function VideoList({ videos, onDelete, onRetry }: Props) {
  if (videos.length === 0) {
    return <div className="card empty">No videos yet. Upload one to create your first diary.</div>;
  }
  return (
    <div className="card video-list">
      {videos.map((v) => {
        const busy = v.status === "queued" || v.status === "processing";
        return (
          <div className="video-row" key={v.video_id} data-testid="video-row">
            <Link to={`/videos/${v.video_id}`} className="thumb" aria-label={`Open ${v.title}`}>
              {v.thumbnail ? <img src={v.thumbnail} alt="" /> : <Film size={28} />}
              {v.duration != null && <span className="duration">{clock(v.duration)}</span>}
            </Link>
            <div style={{ minWidth: 0 }}>
              <Link to={`/videos/${v.video_id}`} className="video-title">{v.title}</Link>
              <div className="video-meta">
                Uploaded {timeAgo(v.uploaded_at)}
                {v.size_bytes ? ` · ${fileSize(v.size_bytes)}` : ""}
                {v.date ? ` · recorded ${v.date}` : ""}
              </div>
            </div>
            <div className="status-cell">
              <div><StatusBadge video={v} /></div>
              {busy && (
                <div className="status-line">
                  <ProgressBar value={v.progress} />
                  <span>{v.progress}%</span>
                </div>
              )}
              {busy && <div className="status-line">{v.step_label}</div>}
              {v.status === "failed" && <div className="status-line error-text" title={v.error ?? ""}>{v.error}</div>}
            </div>
            <div className="row-actions">
              {v.status === "done" && (
                <Link className="btn" to={`/videos/${v.video_id}`}><BookOpen size={16} /> View Diary</Link>
              )}
              {v.status === "failed" && (
                <button className="btn" onClick={() => onRetry(v)}><RotateCw size={16} /> Retry</button>
              )}
              {!busy && (
                <button className="icon-btn" aria-label={`Delete ${v.title}`} onClick={() => onDelete(v)}>
                  <Trash2 size={17} />
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
