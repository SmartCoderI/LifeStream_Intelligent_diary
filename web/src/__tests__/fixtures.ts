import type { AnswerResult, BreakdownItem, LatencySummary, QueryRecord, RunRecord, Span, Timeline, VideoDetail, VideoItem } from "../lib/api";

const now = Date.now() / 1000;

export const processingVideo: VideoItem = {
  video_id: "travel-vlog", title: "travel_vlog", original_name: "travel_vlog.mp4", date: "2026-11-04",
  duration: 624, size_bytes: 532 * 1024 ** 2, uploaded_at: now - 120, status: "processing",
  step: "diarizing", step_label: "Identifying speakers", progress: 45, error: null, type: null, thumbnail: null,
};

export const doneVideo: VideoItem = {
  video_id: "team-meeting", title: "Weekly Student Success Meeting", original_name: "team.mp4",
  date: "2026-11-04", duration: 98, size_bytes: 14 * 1024 ** 2, uploaded_at: now - 3600, status: "done",
  step: "done", step_label: "Completed", progress: 100, error: null, type: "meeting",
  thumbnail: "/api/videos/team-meeting/keyframes/frame_000003253.jpg",
};

export const failedVideo: VideoItem = {
  ...doneVideo, video_id: "broken", title: "broken", status: "failed", step: "failed",
  step_label: "Failed", progress: 0, error: "broken.mp4 has no audio stream; nothing to transcribe.", type: null,
  thumbnail: null,
};

export const meetingDetail: VideoDetail = {
  ...doneVideo,
  summary: {
    video_id: "team-meeting", date: "2026-11-04", duration: 98,
    classification: { type: "meeting", confidence: "high", reason: "five speakers" },
    vlog: null,
    meeting: {
      title: "Weekly Student Success Meeting",
      overview: "Staff discussed Friday absences and one student's situation.",
      location: { value: "Unknown", source: "unknown" },
      attendees: ["SPEAKER_00", "SPEAKER_01"],
      topics: [
        { title: "Friday absenteeism", points: [
          { speaker: "SPEAKER_00", timestamp: "00:00:05", content: "Many students skip on Fridays." },
          { speaker: "SPEAKER_01", timestamp: "00:00:17", content: "Suggested a pancake breakfast." },
        ] },
        { title: "John Smith", points: [
          { speaker: "SPEAKER_01", timestamp: "00:01:00", content: "John is caring for his siblings." },
        ] },
      ],
      decisions: ["Try a pancake breakfast next Friday"],
      action_items: [{ owner: "SPEAKER_01", task: "Put up flu-season posters", timestamp: "00:00:30", due: null }],
    },
  },
  script: [
    { start: 0, end: 15, speaker: "SPEAKER_00", text: "Hello everyone, thanks for coming.", frames: [] },
    { start: 16, end: 29, speaker: "SPEAKER_01", text: "My mentees find Fridays hard.", frames: [] },
  ],
  keyframes: [{ time: 3.2, path: "keyframes/frame_000003253.jpg", url: "/api/videos/team-meeting/keyframes/frame_000003253.jpg" }],
  speakers: [
    { label: "SPEAKER_00", speaker: "SPEAKER_00", name: null, seconds: 15, turns: 1, share: 0.54, first_seen: 0 },
    { label: "SPEAKER_01", speaker: "SPEAKER_01", name: null, seconds: 13, turns: 1, share: 0.46, first_seen: 16 },
  ],
  timings: { total: 106 },
};

export const processingDetail: VideoDetail = {
  ...processingVideo, summary: null, script: [], keyframes: [], speakers: [], timings: null,
};


export const answer: AnswerResult = {
  question: "What did we discuss about the frontend last week?",
  answer: "The navigation bar breaks below 400 pixels because of a hard-coded logo width.",
  found: true,
  date_from: "2026-11-02",
  date_to: "2026-11-08",
  time_phrase: "last week",
  timing: { total: 3.4, spans: [
    { name: "search", label: "Search", kind: "step", parent: null, start: 0, end: 0.2, duration: 0.2, meta: {} },
    { name: "answer", label: "Write answer", kind: "llm", parent: null, start: 0.2, end: 3.4, duration: 3.2, meta: {} },
  ] },
  citations: [{
    video_id: "team-meeting", title: "Frontend Weekly Sync", date: "2026-11-05", kind: "summary",
    section: "Topic: Navigation bar", start: 6, end: 24, snippet: "The mobile layout breaks below 400 pixels.",
    thumbnail: null,
  }],
};

const T = 1_800_000_000; // a fixed epoch second
const CATS: Record<string, Span["category"]> = {
  upload: "upload_queue", queue: "upload_queue", transcribe: "speech", "transcribe.load_model": "model_loading",
  visual: "media", classify: "llm", summarize: "llm", diarize: "speech", "running.transcribing": "speech",
  parse: "other", search: "other", answer: "llm",
};
const span = (name: string, label: string, kind: Span["kind"], start: number, end: number,
              extra: Partial<Span> = {}): Span => ({
  name, label, kind, parent: null, start: T + start, end: T + end, duration: end - start, meta: {},
  category: CATS[name] ?? "other", ...extra,
});
const bd = (category: BreakdownItem["category"], label: string, seconds: number, share: number,
            per_video_minute?: number): BreakdownItem => ({ category, label, seconds, share, per_video_minute });
const runBreakdown = [bd("speech", "Speech models", 50, 0.417), bd("llm", "Claude", 30, 0.25),
  bd("model_loading", "Model loading", 10, 0.083), bd("upload_queue", "Upload & queue", 5, 0.042),
  bd("other", "Other", 25, 0.208)];
const config = { device: "cpu", whisper_model: "medium.en", compute_type: "int8", llm_model: "claude-opus-5-5" };

export const doneTimeline: Timeline = {
  video_id: "team-meeting", status: "done", error: null, started_at: T, finished_at: T + 120, total: 120,
  video_duration: 98, source: "dashboard",
  spans: [
    span("upload", "Upload (browser to server)", "io", 0, 3, { meta: { bytes: 14 * 1024 ** 2 } }),
    span("queue", "Waiting in queue", "io", 3, 5),
    span("transcribe", "Transcribe speech (WhisperX medium.en)", "step", 5, 65, { meta: { words: 357 } }),
    span("transcribe.load_model", "Load Whisper model (medium.en)", "load", 5, 15, { parent: "transcribe" }),
    span("visual", "Scene detection + keyframes (parallel)", "step", 6, 30, { meta: { scenes: 12 } }),
    span("classify", "Classify meeting / vlog (Claude)", "llm", 70, 73,
         { meta: { input_tokens: 4200, output_tokens: 80, model: "claude-opus-5-5" } }),
    span("summarize", "Write meeting summary (Claude)", "llm", 73, 100,
         { meta: { input_tokens: 9100, output_tokens: 2400 } }),
    span("diarize", "Speaker diarization (reused)", "cached", 65, 65),
  ],
  stats: {
    total: 120, upload: 3, queue: 2, processing: 95, llm_seconds: 30, input_tokens: 13300, output_tokens: 2480,
    slowest: { label: "Transcribe speech (WhisperX medium.en)", duration: 60 },
    cached_steps: ["Speaker diarization (reused)"], speed: 0.97,
    breakdown: runBreakdown, bottleneck: runBreakdown[0], config,
  },
};

export const runningTimeline: Timeline = {
  ...doneTimeline, status: "running", finished_at: null,
  spans: [...doneTimeline.spans.slice(0, 2),
          span("running.transcribing", "Transcribing speech", "running", 5, 40, { running: true })],
  stats: { ...doneTimeline.stats!, llm_seconds: 0, input_tokens: 0, output_tokens: 0, cached_steps: [] },
};

export const runs: RunRecord[] = [
  { ...doneTimeline, run_id: "r2", title: "Weekly Student Success Meeting", deleted: false, stats: doneTimeline.stats! },
  { ...doneTimeline, run_id: "r1", title: "Old upload", deleted: true, status: "failed", error: "model crashed",
    started_at: T - 3600, stats: { ...doneTimeline.stats!, slowest: null } },
];

export const queries: QueryRecord[] = [{
  at: T + 500, question: "What did we discuss about the frontend last week?", status: "done", error: null,
  found: true, citations: 2, total: 3.4,
  spans: [span("parse", "Read time phrase", "step", 500, 500.01), span("search", "Search index", "step", 500.01, 500.2),
          span("answer", "Write answer (Claude)", "llm", 500.2, 503.4, { meta: { input_tokens: 2100, output_tokens: 180 } })],
  stats: { ...doneTimeline.stats!, total: 3.4, input_tokens: 2100, output_tokens: 180,
           breakdown: [bd("llm", "Claude", 3.2, 0.94), bd("database", "Database", 0.04, 0.012),
                       bd("embedding", "Embedding", 0.15, 0.044)] },
}];

export const latencySummary: LatencySummary = {
  runs: 3, runs_skipped: 1, config, speed_median: 2.35, total_median: 171,
  breakdown: [bd("speech", "Speech models", 300, 0.55, 76.1), bd("llm", "Claude", 109, 0.2, 27.6),
              bd("model_loading", "Model loading", 44, 0.08, 11.2), bd("other", "Other", 92, 0.17, 23.3)],
  bottleneck: bd("speech", "Speech models", 300, 0.55, 76.1), llm_share: 0.2,
  queries: { count: 12, p50: 2.9, p95: 4.1, llm_p50: 2.7, database_p50: 0.04, embedding_p50: 0.15,
             breakdown: [bd("llm", "Claude", 30, 0.93)] },
};
