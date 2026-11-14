import { ArrowLeft, BookOpen, Check, Clapperboard, FileText, Gauge, Loader2, Play, Square, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, cancellable, type VideoDetail } from "../lib/api";
import SummaryTab from "../components/video/SummaryTab";
import ScenesTab from "../components/video/ScenesTab";
import SpeakersTab from "../components/video/SpeakersTab";
import StatusBadge, { ProgressBar } from "../components/library/StatusBadge";
import TranscriptTab from "../components/video/TranscriptTab";
import TimingPanel from "../components/latency/TimingPanel";
import { clock, parseTimestamp, speakerColors } from "../lib/format";
import { usePolling } from "../lib/usePolling";

type Tab = "summary" | "transcript" | "speakers" | "scenes" | "timing";
const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: "summary", label: "Summary", icon: <BookOpen size={16} /> },
  { id: "transcript", label: "Transcript", icon: <FileText size={16} /> },
  { id: "speakers", label: "Speakers", icon: <Users size={16} /> },
  { id: "scenes", label: "Scenes", icon: <Clapperboard size={16} /> },
  { id: "timing", label: "Timing", icon: <Gauge size={16} /> },
];
const STEPS = [
  ["validating", "Checking the file"],
  ["extracting_audio", "Extracting audio"],
  ["transcribing", "Transcribing speech"],
  ["diarizing", "Identifying speakers"],
  ["detecting_scenes", "Detecting scenes"],
  ["merging", "Building the script"],
  ["classifying", "Classifying video"],
  ["summarizing", "Writing the summary"],
  ["indexing", "Adding to search"],
] as const;

function keyMoments(v: VideoDetail): { time: number; label: string }[] {
  const s = v.summary;
  if (s?.meeting) {
    return s.meeting.topics
      .map((t) => ({ time: parseTimestamp(t.points[0]?.timestamp ?? "") ?? -1, label: t.title }))
      .filter((m) => m.time >= 0);
  }
  if (s?.vlog) {
    return s.vlog.activities.map((a) => ({ time: parseTimestamp(a.time) ?? -1, label: a.description }))
      .filter((m) => m.time >= 0).slice(0, 8);
  }
  return [];
}

function Processing({ video, onChange }: { video: VideoDetail; onChange: () => void }) {
  const currentIndex = STEPS.findIndex(([id]) => id === video.step);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const cancel = async () => {
    if (!window.confirm(`Cancel processing "${video.title}"? You can retry it later from the Videos page.`)) return;
    try {
      await api.cancelVideo(video.video_id);
      setCancelError(null);
    } catch (e) {
      setCancelError((e as Error).message);
    }
    onChange();
  };
  return (
    <div className="card status-card">
      <div className="status-head">
        <StatusBadge video={video} />
        {cancellable(video) && <button className="btn" onClick={cancel}><Square size={14} /> Cancel</button>}
      </div>
      {cancelError && <p className="error-text" role="alert" style={{ margin: 0 }}>{cancelError}</p>}
      {video.status === "cancelled" ? (
        <p className="muted" style={{ margin: 0 }}>Cancelled. Retry or delete it from the <Link to="/videos">Videos</Link> page.</p>
      ) : video.status === "failed" ? (
        <p className="error-text" role="alert" style={{ margin: 0 }}>{video.error}</p>
      ) : (
        <>
          <ProgressBar value={video.progress} />
          <ol className="steps">
            {STEPS.map(([id, label], i) => {
              const state = video.status === "queued" ? "" : i < currentIndex ? "done" : i === currentIndex ? "current" : "";
              return (
                <li key={id} className={state}>
                  {state === "done" ? <Check size={15} /> : state === "current" ? <Loader2 size={15} className="spin" /> : <span style={{ width: 15 }} />}
                  {label}
                </li>
              );
            })}
          </ol>
          <p className="muted" style={{ margin: 0 }}>
            Speech recognition and speaker identification take a while (on a laptop, roughly as
            long as the video). You can leave this page.
          </p>
        </>
      )}
    </div>
  );
}

export default function VideoPage() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const startAt = Number(params.get("t") ?? "");
  const { data: video, error, refresh } = usePolling(() => api.getVideo(id), (v) => v.status === "queued" || v.status === "processing");
  const [tab, setTab] = useState<Tab>("summary");
  const [time, setTime] = useState(0);
  const player = useRef<HTMLVideoElement>(null);

  const colors = useMemo(() => {
    const order = [...(video?.speakers ?? [])].sort((a, b) => a.first_seen - b.first_seen).map((s) => s.speaker);
    return speakerColors(order);
  }, [video?.speakers]);

  const seek = (seconds: number) => {
    const el = player.current;
    if (!el) return;
    el.currentTime = seconds;
    el.play().catch(() => { /* autoplay may be blocked; position is still set */ });
  };

  // opened from a search source (?t=seconds): start the player there
  const ready = video?.status === "done" && !!video.summary;
  useEffect(() => {
    const el = player.current;
    if (!ready || !el || !(startAt > 0)) return;
    const jump = () => { el.currentTime = startAt; };
    if (el.readyState >= 1) jump();
    else el.addEventListener("loadedmetadata", jump, { once: true });
  }, [ready, startAt]);

  if (error) return <div className="card empty error-text" role="alert">{error}</div>;
  if (!video) return <div className="card empty">Loading…</div>;

  return (
    <>
      <div className="back-row">
        <Link to="/videos" className="icon-btn" aria-label="Back to videos"><ArrowLeft size={20} /></Link>
        <h1>{video.title}</h1>
        {!ready && <StatusBadge video={video} />}
      </div>

      {!ready ? (
        <>
          <Processing video={video} onChange={refresh} />
          <section className="card live-timing">
            <h3>Timing so far</h3>
            <TimingPanel videoId={video.video_id} />
          </section>
        </>
      ) : (
        <>
          <div className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} role="tab" aria-selected={tab === t.id}
                      className={`tab${tab === t.id ? " active" : ""}`} onClick={() => setTab(t.id)}>
                {t.icon} {t.label}
              </button>
            ))}
          </div>
          <div className="video-layout">
            <div className="player-col">
              <video ref={player} className="player" src={api.mediaUrl(video.video_id)} controls preload="metadata"
                     poster={video.thumbnail ?? undefined} onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} />
              {keyMoments(video).length > 0 && (
                <div className="card moments">
                  <h3>Key Moments</h3>
                  {keyMoments(video).map((m, i) => (
                    <button className="moment" key={i} onClick={() => seek(m.time)}>
                      <Play size={14} className="play-dot" fill="currentColor" />
                      <span className="ts">{clock(m.time)}</span>
                      <span>{m.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div>
              {tab === "summary" && <SummaryTab summary={video.summary!} colors={colors} onSeek={seek} />}
              {tab === "transcript" && <TranscriptTab script={video.script} colors={colors} currentTime={time} onSeek={seek} />}
              {tab === "speakers" && <SpeakersTab videoId={video.video_id} speakers={video.speakers} colors={colors} onSaved={refresh} />}
              {tab === "scenes" && <ScenesTab keyframes={video.keyframes} onSeek={seek} />}
              {tab === "timing" && <TimingPanel videoId={video.video_id} />}
            </div>
          </div>
        </>
      )}
    </>
  );
}
