/** 75.4 -> "01:15", 3725 -> "1:02:05" */
export function clock(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds)) return "--:--";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${String(m).padStart(2, "0")}:${sec}`;
}

/** "HH:MM:SS" | "MM:SS" | "SS" -> seconds, or null */
export function parseTimestamp(text: string): number | null {
  const parts = text.trim().split(":");
  if (parts.length < 1 || parts.length > 3 || parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  return parts.reduce((total, p) => total * 60 + Number(p), 0);
}

export function fileSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024 ** 2) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 ** 3) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function timeAgo(epochSeconds: number, now: number = Date.now() / 1000): string {
  const d = Math.max(0, now - epochSeconds);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) {
    const h = Math.floor(d / 3600);
    return `${h} hour${h === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(d / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "short", year: "numeric", month: "short", day: "numeric",
  });
}

export function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const SPEAKER_COLORS = ["#6d4aff", "#0e9f6e", "#e8590c", "#1c7ed6", "#c2255c", "#7c5c00", "#0b7285", "#5f3dc4"];

/** Stable color per speaker label, in order of first appearance. */
export function speakerColors(speakersInOrder: string[]): Record<string, string> {
  const map: Record<string, string> = {};
  speakersInOrder.forEach((s, i) => (map[s] = SPEAKER_COLORS[i % SPEAKER_COLORS.length]));
  return map;
}
