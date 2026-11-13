import type { Category, RunConfig } from "./api";

/** Display order = color order (validated palette slots 1-6; the last two are neutral). */
export const CATEGORY_ORDER: Category[] = [
  "speech", "llm", "model_loading", "media", "embedding", "database", "upload_queue", "other",
];

export const CATEGORY_HELP: Record<Category, string> = {
  speech: "Transcription, word alignment and speaker identification (WhisperX, pyannote)",
  llm: "LLM calls (Qwen or Claude): classification, summaries, answers",
  model_loading: "Loading ML models into memory (Whisper, pyannote, embedding model)",
  media: "FFmpeg / ffprobe work and scene detection",
  embedding: "Turning text into vectors for search",
  database: "Postgres + pgvector reads and writes",
  upload_queue: "Upload, saving the file, waiting in the queue",
  other: "Everything else: merging, rendering, gaps between steps",
};

export function configLine(c: RunConfig | null | undefined): string {
  if (!c) return "";
  return [c.device?.toUpperCase(), c.whisper_model && `${c.whisper_model}${c.compute_type ? ` ${c.compute_type}` : ""}`,
          c.llm_model].filter(Boolean).join(" · ");
}
