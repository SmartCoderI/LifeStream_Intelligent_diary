import type { Category, ParallelWork, RunConfig } from "./api";
import { duration } from "./format";

/** Display order = pipeline order = color order (validated palette slots 1-8; the two ends are neutral). */
export const CATEGORY_ORDER: Category[] = [
  "transfer", "cold_start", "media", "model_loading", "transcription", "speakers", "llm", "embedding", "database",
  "other",
];

export const CATEGORY_LABELS: Record<Category, string> = {
  transfer: "Upload & transfers", cold_start: "Cold start & queue", media: "FFmpeg & scenes",
  model_loading: "Model loading", transcription: "Transcription (WhisperX)", speakers: "Speakers (pyannote)",
  llm: "LLM (Claude)", embedding: "Embedding", database: "Database", other: "Other steps & waits",
};

export const CATEGORY_HELP: Record<Category, string> = {
  transfer: "Uploading from the browser; in the cloud also moving files between S3, the GPU worker and the dashboard server",
  cold_start: "Cloud: waiting for a GPU machine, booting it, pulling the worker image, starting the container. This computer: waiting in the queue",
  media: "Checking the file and extracting audio (FFmpeg), plus any scene-detection time that wasn't hidden behind the audio",
  model_loading: "Loading Whisper, the alignment model, pyannote and the embedding model into memory",
  transcription: "Speech to text with word timestamps (WhisperX)",
  speakers: "Who spoke when (pyannote); runs after transcription",
  llm: "Claude: classifying the video, writing the summary, answering questions",
  embedding: "Turning transcript and summary text into vectors for search",
  database: "Postgres + pgvector reads and writes",
  other: "Building the script, writing Markdown and the diary file, and waits between steps (e.g. the server noticing a finished job)",
};

/** "Scene detection ran alongside other steps: 60 s, all of it hidden" */
export function parallelNote(items: ParallelWork[] | undefined): string | null {
  if (!items?.length) return null;
  return items.map((p) => {
    const shown = p.duration - p.hidden;
    return `${p.label} ran alongside other steps for ${duration(p.duration)}; ` +
      (shown < 0.5 ? "all of it was hidden, so it adds no time" : `${duration(shown)} of it added to the total`);
  }).join(" · ");
}

export function configLine(c: RunConfig | null | undefined): string {
  if (!c) return "";
  return [c.device?.toUpperCase(), c.whisper_model && `${c.whisper_model}${c.compute_type ? ` ${c.compute_type}` : ""}`,
          c.llm_model].filter(Boolean).join(" · ");
}
