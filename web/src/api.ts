// Types mirror the Python API (src/lifestream/api.py).

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
export interface SpeakerStat { speaker: string; seconds: number; turns: number; share: number; first_seen: number }

export interface VideoDetail extends VideoItem {
  summary: VideoSummary | null;
  script: ScriptSegment[];
  keyframes: Keyframe[];
  speakers: SpeakerStat[];
  timings: Record<string, number> | null;
}

export interface DayItem { date: string; video_count: number }
export interface DayDetail { date: string; videos: (VideoItem & { summary: VideoSummary })[]; markdown: string | null }

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  listVideos: () => get<VideoItem[]>("/api/videos"),
  getVideo: (id: string) => get<VideoDetail>(`/api/videos/${id}`),
  listDays: () => get<DayItem[]>("/api/days"),
  getDay: (date: string) => get<DayDetail>(`/api/days/${date}`),
  mediaUrl: (id: string) => `/api/videos/${id}/media`,

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
