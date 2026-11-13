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
/** Kind of work; a run's or query's categories add up to its wall-clock time. */
export type Category =
  "transfer" | "cold_start" | "media" | "model_loading" | "transcription" | "speakers" | "llm" | "embedding"
  | "database" | "other";
export type Environment = "cloud" | "local";
export interface BreakdownItem {
  category: Category; label: string; seconds: number; share: number; per_video_minute?: number | null;
  /** across runs: median seconds per run */
  median?: number | null;
}
/** A line of the Stage 3 time budget (targets are for a 1-hour video on the cloud GPU). */
export interface BudgetRow { line: string; label: string; seconds: number; target: string; within: boolean }
export interface BudgetLine { line: string; label: string; target: string; median: number | null }
/** a step that ran alongside others: its full duration and how much of it was hidden */
export interface ParallelWork { label: string; duration: number; hidden: number }
/** a cloud run of a ~1-hour video checked against the Stage 3 time budget (M3a) */
export interface BudgetCheck { rows: BudgetRow[]; total: number; total_target: string; passed: boolean; video_duration: number }
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
  /** cloud: AWS GPU worker; local: this computer */
  environment?: Environment;
  /** waiting before processing: cloud GPU machine + image, or the queue on this computer */
  cold_start?: number | null;
  cold?: boolean | null;
  budget?: BudgetCheck | null;
  parallel?: ParallelWork[];
}
export interface RunConfig {
  device?: string; whisper_model?: string; compute_type?: string; diarization_device?: string;
  llm_model?: string | null; embed_model?: string;
}
export interface LatencySummary {
  /** run numbers cover one environment; queries cover all */
  environment?: Environment; environments?: Record<Environment, number>; cold_start_median?: number | null;
  budget?: BudgetCheck | null; budget_lines?: BudgetLine[] | null;
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

/** Cloud mode (Stage 3): uploads go straight to S3 and processing runs on a cloud GPU. */
export interface AppConfig { mode: "local" | "cloud"; auth: boolean }
interface CloudUpload extends VideoItem { presigned_put_url: string; upload_headers: Record<string, string> }

export class AuthError extends Error {}

export const MAX_UPLOAD_BYTES = { local: 1024 ** 3, cloud: 5 * 1024 ** 3 };

const KEY_COOKIE = "lifestream_key";

/** The API key travels as a cookie so <img> and <video> requests carry it too. */
export function saveApiKey(key: string) {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${KEY_COOKIE}=${encodeURIComponent(key)}; path=/; max-age=31536000; SameSite=Strict${secure}`;
}

let configPromise: Promise<AppConfig> | null = null;

/** Loaded once per page load. */
export function loadConfig(): Promise<AppConfig> {
  configPromise ??= fetch("/api/config")
    .then((r) => (r.ok ? r.json() : { mode: "local", auth: false }))
    .catch(() => ({ mode: "local", auth: false }));
  return configPromise;
}

/** Tests only. */
export function resetConfig() { configPromise = null; }

function fail(status: number, detail: string | undefined, fallback: string): never {
  if (status === 401) throw new AuthError(detail || "API key required");
  throw new Error(detail || fallback);
}

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
    fail(res.status, body.detail, `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function send<T>(method: string, url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) fail(res.status, data.detail, `${res.status} ${res.statusText}`);
  return data as T;
}

/** XHR (not fetch) so the upload can report progress. */
function xhrUpload(method: string, url: string, body: Document | XMLHttpRequestBodyInit,
                   headers: Record<string, string>, onProgress: (f: number) => void): Promise<XMLHttpRequest> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => resolve(xhr);
    xhr.onerror = () => reject(new Error("Upload failed: can't reach the server"));
    xhr.send(body);
  });
}

export const api = {
  listVideos: () => get<VideoItem[]>("/api/videos"),
  getVideo: (id: string) => get<VideoDetail>(`/api/videos/${id}`),
  mediaUrl: (id: string) => `/api/videos/${id}/media`,
  searchStatus: () => get<SearchStatus>("/api/search/status"),
  getTimeline: (id: string) => get<Timeline>(`/api/videos/${id}/timeline`),
  latencyRuns: (limit = 50) => get<RunRecord[]>(`/api/latency/runs?limit=${limit}`),
  latencyQueries: (limit = 50) => get<QueryRecord[]>(`/api/latency/queries?limit=${limit}`),
  latencySummary: (limit = 20, environment?: Environment | null) =>
    get<LatencySummary>(`/api/latency/summary?limit=${limit}${environment ? `&environment=${environment}` : ""}`),

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

  /** Upload with progress. Local: the file goes to this server. Cloud: the server returns a
   *  presigned S3 link, the file goes straight to S3, and the GPU job starts on its own. */
  async uploadVideo(file: File, date: string, onProgress: (fraction: number) => void): Promise<VideoItem> {
    const config = await loadConfig();
    if (config.mode === "cloud") {
      const video = await send<CloudUpload>("POST", "/api/videos", { date, filename: file.name, size_bytes: file.size });
      const put = await xhrUpload("PUT", video.presigned_put_url, file, video.upload_headers, onProgress);
      if (put.status < 200 || put.status >= 300) throw new Error(`Upload to storage failed (${put.status})`);
      return send<VideoItem>("POST", `/api/videos/${video.video_id}/uploaded`, {});
    }
    const form = new FormData();
    form.append("file", file);
    form.append("date", date);
    form.append("client_started_at", String(Date.now()));  // upload span of the latency timeline
    const xhr = await xhrUpload("POST", "/api/videos", form, {}, onProgress);
    let body: any = {};
    try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
    if (xhr.status < 200 || xhr.status >= 300) fail(xhr.status, body.detail, `Upload failed (${xhr.status})`);
    return body;
  },
};
