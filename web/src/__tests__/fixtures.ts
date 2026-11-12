import type { AnswerResult, DayDetail, VideoDetail, VideoItem } from "../lib/api";

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

export const day: DayDetail = {
  date: "2026-11-04",
  markdown: "# Daily Summary: 2026-11-04",
  videos: [{ ...doneVideo, summary: meetingDetail.summary! }],
};

export const answer: AnswerResult = {
  question: "What did we discuss about the frontend last week?",
  answer: "The navigation bar breaks below 400 pixels because of a hard-coded logo width.",
  found: true,
  date_from: "2026-11-02",
  date_to: "2026-11-08",
  time_phrase: "last week",
  citations: [{
    video_id: "team-meeting", title: "Frontend Weekly Sync", date: "2026-11-05", kind: "summary",
    section: "Topic: Navigation bar", start: 6, end: 24, snippet: "The mobile layout breaks below 400 pixels.",
    thumbnail: null,
  }],
};
