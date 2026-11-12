"""F3: speech recognition with word-level timestamps (WhisperX)."""

from __future__ import annotations

import gc
from contextlib import nullcontext
from pathlib import Path

from ..config import Settings
from ..schemas import Word


def words_from_aligned(aligned: dict) -> list[Word]:
    """Flatten WhisperX aligned output into words, filling in missing timestamps.

    The aligner can't time some tokens (numbers, symbols). Those get the end of
    the previous word as their start, and the start of the next timed word (or
    their own start) as their end, so every word has a time.
    """
    raw: list[dict] = []
    for seg in aligned.get("segments", []):
        for w in seg.get("words", []):
            text = str(w.get("word", "")).strip()
            if text:
                raw.append({"text": text, "start": w.get("start"), "end": w.get("end"),
                            "seg_start": seg.get("start", 0.0)})

    words: list[Word] = []
    prev_end = 0.0
    for i, w in enumerate(raw):
        start = w["start"]
        if start is None:
            start = max(prev_end, float(w["seg_start"] or 0.0))
        end = w["end"]
        if end is None:
            nxt = next((r["start"] for r in raw[i + 1:] if r["start"] is not None), None)
            end = max(start, float(nxt)) if nxt is not None else start
        start, end = float(start), float(end)
        if end < start:
            end = start
        words.append(Word(start=start, end=end, text=w["text"]))
        prev_end = end
    return words


def transcribe(wav: str | Path, settings: Settings, timings=None) -> list[Word]:
    """Transcribe English speech and align each word to the audio.
    With `timings`, model loading and each stage are recorded as sub-steps."""
    import whisperx

    def sub(name: str, label: str, kind: str = "step"):
        if timings is None:
            return nullcontext({"meta": {}})
        return timings.step(f"transcribe.{name}", label, kind=kind, parent="transcribe")

    audio = whisperx.load_audio(str(wav))

    with sub("load_model", f"Load Whisper model ({settings.whisper_model})", "load") as span:
        span["meta"]["cold"] = True  # freed after each video to save memory
        model = whisperx.load_model(
            settings.whisper_model, settings.device,
            compute_type=settings.compute_type, language=settings.language,
        )
    with sub("asr", "Speech to text") as span:
        result = model.transcribe(audio, batch_size=settings.asr_batch_size, language=settings.language)
        span["meta"]["segments"] = len(result.get("segments", []))
    del model
    gc.collect()

    with sub("load_align_model", "Load alignment model", "load"):
        align_model, metadata = whisperx.load_align_model(language_code=settings.language, device=settings.device)
    with sub("align", "Align word timestamps") as span:
        aligned = whisperx.align(
            result["segments"], align_model, metadata, audio, settings.device,
            return_char_alignments=False,
        )
        words = words_from_aligned(aligned)
        span["meta"]["words"] = len(words)
    del align_model
    gc.collect()

    return words
