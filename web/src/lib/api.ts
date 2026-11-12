// Types mirror the Python API (src/lifestream/api/app.py).

export type Status = "queued" | "processing" | "done" | "failed" | "unknown";

export interface VideoItem {
  video_id: string;
  title: string;
  original_name: string;
  date: string | null;
  duration: number | null;
  size_bytes: number | null;
  uploaded_at: number;
  status: Status;
  step: string | null;
  step_label: string;
  progress: number;
  error: string | null;
  type: "meeting" | "vlog" | null;
  thumbnail: string | null;
}

export interface Location { value: string; source: "metadata" | "inferred" | "unknown" }
export interface MeetingPoint { speaker: string; timestamp: string; content: string }
export interface MeetingTopic { title: string; points: MeetingPoint[] }
export interface MeetingActionItem { owner: string; task: string; timestamp: string; due: string | null }
export interface MeetingSummary {
  title: string;
  overview: string;
  location: Location;
  attendees: string[];
  topics: MeetingTopic[];
  decisions: string[];
  action_items: MeetingActionItem[];
}
export interface Activity { time: string; description: string }
export interface VlogSummary {
  title: string;
  overview: string;
  location: Location;
  activities: Activity[];
  action_items: { owner: string | null; task: string }[];
}
export interface VideoSummary {
  video_id: string;
  date: string;
  duration: number;
  classification: { type: "meeting" | "vlog"; confidence: string; reason: string };
  meeting: MeetingSummary | null;
  vlog: VlogSummary | null;
}
export interface ScriptSegment { start: number; end: number; speaker: string; text: string; frames: string[] }
export interface Keyframe { time: number; path: string; url: string }
/** `speaker` is the display name (registered name or label); `label` is the raw diarization label. */
export interface SpeakerStat {
  label: string; speaker: string; name: string | null;
  seconds: number; turns: number; share: number; first_seen: number;
}

export interface Citation {
  video_id: string; title: string; date: string; kind: "summary" | "transcript"; section: string;
  start: number; end: number; snippet: string; thumbnail: string | null;
}
export interface AnswerResult {
  question: string; answer: string; found: boolean; citations: Citation[];
  date_from: string | null; date_to: string | null; time_phrase: string | null;
  timing?: { total: number; spans: Span[]; breakdown?: BreakdownItem[] };
}

// ---------- latency ----------
export type SpanKind = "io" | "step" | "load" | "llm" | "cached" | "running";
export type Category =
  "speech" | "llm" | "model_loading" | "media" | "embedding" | "database" | "upload_queue" | "other";
export interface BreakdownItem {
  category: Category; label: string; seconds: number; share: number; per_video_minute?: number | null;
}
export interface Span {
  name: string; label: string; kind: SpanKind; parent: string | null; category?: Category;
  start: number; end: number; duration: number;  // epoch seconds; duration in seconds
  meta: Record<string, unknown>; running?: boolean;
}
export interface RunStats {
  total: number | null; upload: number | null; queue: number | null; processing: number;
  llm_seconds: number; input_tokens: number; output_tokens: number;
  slowest: { label: string; duration: number } | null; cached_steps: string[]; speed: number | null;
  breakdown: BreakdownItem[]; bottleneck: BreakdownItem | null; config: RunConfig | null;
}
export interface RunConfig {
  device?: string; whisper_model?: string; compute_type?: string; diarization_device?: string;
  llm_model?: string | null; embed_model?: string;
}
export interface LatencySummary {
  runs: number; runs_skipped: number; config: RunConfig | null;
  speed_median: number | null; total_median: number | null;
  breakdown: BreakdownItem[]; bottleneck: BreakdownItem | null; llm_share: number;
  queries: {
    count: number; p50: number | null; p95: number | null;
    llm_p50: number | null; database_p50: number | null; embedding_p50: number | null;
    breakdown: BreakdownItem[];
  };
}
export interface Timeline {
  video_id: string; status: "running" | "queued" | "done" | "failed"; error: string | null;
  started_at: number; finished_at: number | null; total: number; video_duration: number | null;
  spans: Span[]; stats: RunStats | null; source?: string; now?: number;
}
export interface RunRecord extends Timeline {
  run_id: string; title: string; deleted: boolean; file?: string; stats: RunStats;
}
export interface QueryRecord {
  at: number; question: string; status: "done" | "failed"; error: string | null;
  found: boolean | null; citations: number; total: number; spans: Span[]; stats: RunStats;
}
export interface SearchStatus { available: boolean; videos: number; chunks: number; error: string | null }

export interface VideoDetail extends VideoItem {
  summary: VideoSummary | null;
  script: ScriptSegment[];
  keyframes: Keyframe[];
  speakers: SpeakerStat[];
  timings: Record<string, number> | null;
}


async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function send<T>(method: string, url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
  return data as T;
}

export const api = {
  listVideos: () => get<VideoItem[]>("/api/videos"),
  getVideo: (id: string) => get<VideoDetail>(`/api/videos/${id}`),
  mediaUrl: (id: string) => `/api/videos/${id}/media`,
  searchStatus: () => get<SearchStatus>("/api/search/status"),
  getTimeline: (id: string) => get<Timeline>(`/api/videos/${id}/timeline`),
  latencyRuns: (limit = 50) => get<RunRecord[]>(`/api/latency/runs?limit=${limit}`),
  latencyQueries: (limit = 50) => get<QueryRecord[]>(`/api/latency/queries?limit=${limit}`),
  latencySummary: (limit = 20) => get<LatencySummary>(`/api/latency/summary?limit=${limit}`),

  ask: (question: string) => send<AnswerResult>("POST", "/api/query", { question }),

  setSpeakers: (id: string, names: Record<string, string>) =>
    send<{ speakers: SpeakerStat[]; indexed: boolean; index_error: string | null }>(
      "PUT", `/api/videos/${id}/speakers`, { names }),

  async retryVideo(id: string): Promise<void> {
    const res = await fetch(`/api/videos/${id}/retry`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Retry failed (${res.status})`);
    }
  },

  async deleteVideo(id: string): Promise<void> {
    const res = await fetch(`/api/videos/${id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Delete failed (${res.status})`);
    }
  },

  /** Upload with progress (fetch can't report upload progress, XHR can). */
  uploadVideo(file: File, date: string, onProgress: (fraction: number) => void): Promise<VideoItem> {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      form.append("date", date);
      form.append("client_started_at", String(Date.now()));  // upload span of the latency timeline
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/videos");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        let body: any = {};
        try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(body.detail || `Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Upload failed: can't reach the server"));
      xhr.send(form);
    });
  },
};
