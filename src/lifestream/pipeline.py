"""Runs Stage 1 end to end for one video.

    video -> F1 probe + audio -> [audio branch: F3 ASR, F2 diarization]
                              -> [visual branch: F4 scenes + keyframes]   (parallel)
          -> F3 merge into turns -> F5 attach frames -> F6 classify + summarize
          -> daily diary -> search index (Stage 2)

Each step's output is saved in the video's folder. On a re-run, finished
steps are loaded from disk instead of recomputed (use force=True to redo).
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import re
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import TypeAdapter

from .intelligence import summarize
from .processing import asr, diarize, media, merge, scenes, sync
from .config import Settings
from .intelligence.llm import LLMClient, make_llm
from .memory.registry import SpeakerRegistry, apply_names
from .schemas import Keyframe, Scene, ScriptSegment, SpeakerSegment, VideoSummary, Word
from .timing import Timings, append_log

IndexFn = Callable[[Path, Settings], "int | None"]
WordsFn = Callable[[Path, Settings], list[Word]]
DiarizeFn = Callable[..., list[SpeakerSegment]]
VisualFn = Callable[..., tuple[list[Scene], list[Keyframe]]]


@dataclass
class Components:
    """The heavy steps, swappable for fakes in tests."""

    transcribe: WordsFn = asr.transcribe
    diarize: DiarizeFn = diarize.diarize
    visual: VisualFn = scenes.run_visual_branch
    index: IndexFn | None = None  # None = the real search index (memory.indexer.index_output)


def video_id_for(path: str | Path, out_root: str | Path | None = None) -> str:
    """Readable ID from the file name. If another file already uses that ID in
    out_root (e.g. clip.mp4 and clip.mov), add a short hash of the full path."""
    slug = re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-")
    slug = slug[:48].strip("-") or "video"
    if out_root is not None:
        meta = Path(out_root) / slug / "metadata.json"
        if meta.exists():
            owner = json.loads(meta.read_text()).get("path")
            if owner and Path(owner).resolve() != Path(path).resolve():
                digest = hashlib.sha1(str(Path(path).resolve()).encode()).hexdigest()[:6]
                slug = f"{slug}-{digest}"
    return slug


def _save_list(path: Path, items: list) -> None:
    path.write_text(json.dumps([i.model_dump() for i in items], indent=2))


def _load_list(path: Path, model) -> list:
    return TypeAdapter(list[model]).validate_json(path.read_text())


def _timed_visual(fn: VisualFn, **kwargs):
    """Runs in the child process; returns wall-clock start/end for the timeline."""
    start = time.time()
    result = fn(**kwargs)
    return result, start, time.time()


def _diarize_step(fn: DiarizeFn, wav: Path, settings: Settings, timings: Timings, path: Path,
                  hints: dict, parallel: bool) -> list[SpeakerSegment]:
    """F2 as one timed step; saves its result. `parallel`: running in a thread alongside
    transcription, so the timeline only counts the part nothing else was running."""
    label = "Speaker diarization (pyannote, alongside transcription)" if parallel \
        else "Speaker diarization (pyannote)"
    with timings.step("diarize", label) as span:
        segments = fn(wav, settings, timings=timings, **hints)
        span["meta"]["speakers"] = len({s.speaker for s in segments})
        if parallel:
            span["meta"]["parallel"] = True
    _save_list(path, segments)
    return segments


def runs_log_path(out_root: str | Path) -> Path:
    return Path(out_root) / "_logs" / "runs.jsonl"


def process_video(
    video: str | Path,
    date: str,
    out_root: str | Path,
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    components: Components | None = None,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    force: bool = False,
    run_llm: bool = True,
    parallel: bool = True,
    log: Callable[[str], None] = print,
    video_id: str | None = None,
    on_step: Callable[[str], None] | None = None,
    extra_spans: list[dict] | None = None,
    source: str = "cli",
) -> Path:
    """Process one video. Returns its output folder.

    `on_step(name)` is called as each step starts (dashboard progress).
    `extra_spans` are timeline spans measured before processing (upload, queue).
    The run's timeline is saved to timeline.json and appended to the run history,
    whether it finishes or fails.
    """
    video = Path(video)
    out_root = Path(out_root)
    vid = video_id or video_id_for(video, out_root)
    out = out_root / vid
    out.mkdir(parents=True, exist_ok=True)
    timings = Timings(out / "timeline.json", extra_spans,
                      meta={"video_id": vid, "run_id": uuid.uuid4().hex[:8], "source": source,
                            "file": video.name})
    try:
        _run_steps(video, date, out_root, out, vid, settings, timings, llm=llm,
                   components=components or Components(), num_speakers=num_speakers,
                   min_speakers=min_speakers, max_speakers=max_speakers, force=force,
                   run_llm=run_llm, parallel=parallel, log=log, step=on_step or (lambda name: None))
    except Exception as e:
        status = "cancelled" if getattr(e, "cancelled", False) else "failed"
        append_log(runs_log_path(out_root), timings.finish(status, str(e) or type(e).__name__))
        raise
    record = timings.finish("done")
    append_log(runs_log_path(out_root), record)
    timings.save(out / "timings.json", total=record["total"])
    return out


def _run_steps(video: Path, date: str, out_root: Path, out: Path, vid: str, settings: Settings,
               timings: Timings, *, llm, components: Components, num_speakers, min_speakers,
               max_speakers, force: bool, run_llm: bool, parallel: bool,
               log: Callable[[str], None], step: Callable[[str], None]) -> None:
    cached: list[str] = []
    parallel_audio = parallel and settings.parallel_audio
    timings.meta["config"] = {
        "device": settings.device, "whisper_model": settings.whisper_model,
        "compute_type": settings.compute_type, "diarization_device": settings.diarization_device,
        "asr_batch_size": settings.asr_batch_size, "parallel_audio": parallel_audio,
        "diarization_step": settings.diarization_step,
        "llm_model": settings.active_llm_model if run_llm else None, "embed_model": settings.embed_model,
    }

    # ---- F1: validate + metadata + audio ----
    step("validating")
    with timings.step("probe", "Check file (ffprobe)"):
        info = media.probe(video)
    timings.meta["video_duration"] = info.duration
    (out / "metadata.json").write_text(json.dumps(
        {"video_id": vid, "date": date, **info.model_dump()}, indent=2))
    log(f"[{vid}] {info.duration / 60:.1f} min, {info.width}x{info.height}, {info.fps:.2f} fps")

    wav = out / "audio.wav"
    if wav.exists() and not force:
        cached.append("extract_audio")
        timings.cached("extract_audio", "Extract audio (reused)")
    else:
        step("extracting_audio")
        with timings.step("extract_audio", "Extract audio (FFmpeg)"):
            media.extract_audio(video, wav)

    # ---- F4 visual branch, started in parallel with the audio branch ----
    scenes_path, frames_path = out / "scenes.json", out / "keyframes.json"
    visual_kwargs = dict(
        video=str(video), out_dir=str(out), duration=info.duration,
        threshold=settings.scene_threshold, min_scene_len_s=settings.min_scene_len_s,
        max_keyframes=settings.max_keyframes, keyframe_width=settings.keyframe_width,
        sample_fps=settings.scene_sample_fps,
    )
    visual_cached = scenes_path.exists() and frames_path.exists() and not force
    pool = None
    visual_future = None
    visual_saved = visual_cached
    if visual_cached:
        cached.append("visual_branch")
        timings.cached("visual", "Scene detection + keyframes (reused)")
    elif parallel:
        # separate process: true parallelism, and keeps OpenCV out of the ASR process
        pool = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
        visual_future = pool.submit(_timed_visual, components.visual, **visual_kwargs)
        log(f"[{vid}] scene detection started in parallel")

    diar_pool = None
    try:
        # ---- audio branch: F3 ASR and F2 diarization ----
        # They only meet at the merge. On a GPU, diarization runs in a thread alongside
        # transcription (both models fit on the T4); otherwise one after the other.
        audio_start = time.perf_counter()
        words_path = out / "words.json"
        diar_path = out / "diarization.json"
        diar_cached = diar_path.exists() and not force
        diar_hints = dict(num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers)
        diar_future = None
        if not diar_cached and parallel_audio:
            diar_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diarize")
            diar_future = diar_pool.submit(_diarize_step, components.diarize, wav, settings, timings,
                                           diar_path, diar_hints, True)
            log(f"[{vid}] diarization started alongside transcription")

        if words_path.exists() and not force:
            words = _load_list(words_path, Word)
            cached.append("transcribe")
            timings.cached("transcribe", "Transcribe speech (reused)")
        else:
            step("transcribing")
            log(f"[{vid}] transcribing ({settings.whisper_model}, {settings.device})...")
            with timings.step("transcribe", f"Transcribe speech (WhisperX {settings.whisper_model})") as span:
                words = components.transcribe(wav, settings, timings=timings)
                span["meta"]["words"] = len(words)
            _save_list(words_path, words)

        if diar_cached:
            speaker_segments = _load_list(diar_path, SpeakerSegment)
            cached.append("diarize")
            timings.cached("diarize", "Speaker diarization (reused)")
        elif diar_future is not None:
            if not diar_future.done():
                step("diarizing")  # the dashboard shows what is still being waited on
                log(f"[{vid}] waiting for diarization...")
            speaker_segments = diar_future.result()
        else:
            step("diarizing")
            log(f"[{vid}] diarizing...")
            speaker_segments = _diarize_step(components.diarize, wav, settings, timings, diar_path,
                                             diar_hints, False)
        timings.record("audio_branch", time.perf_counter() - audio_start)

        # ---- wait for the visual branch ----
        step("detecting_scenes")
        if visual_cached:
            scene_list = _load_list(scenes_path, Scene)
            keyframes = _load_list(frames_path, Keyframe)
        else:
            if visual_future is not None:
                (scene_list, keyframes), v_start, v_end = visual_future.result()
                label = "Scene detection + keyframes (parallel)"
            else:
                (scene_list, keyframes), v_start, v_end = _timed_visual(components.visual, **visual_kwargs)
                label = "Scene detection + keyframes"
            timings.add("visual", label, v_start, v_end,
                        meta={"scenes": len(scene_list), "keyframes": len(keyframes),
                              "parallel": visual_future is not None})
            timings.record("visual_branch", v_end - v_start)
            _save_list(scenes_path, scene_list)
            _save_list(frames_path, keyframes)
            visual_saved = True
    finally:
        if diar_pool is not None:
            diar_pool.shutdown(wait=True)  # its result is saved by _diarize_step even if ASR failed
        if pool is not None:
            pool.shutdown(wait=True)
            # if the audio branch failed, still keep the finished visual results
            if not visual_saved and visual_future.done() and visual_future.exception() is None:
                (v_scenes, v_frames), _, _ = visual_future.result()
                _save_list(scenes_path, v_scenes)
                _save_list(frames_path, v_frames)
    log(f"[{vid}] {len(words)} words, {len({s.speaker for s in speaker_segments})} speakers, "
        f"{len(scene_list)} scenes, {len(keyframes)} keyframes")

    # ---- F3 merge + F5 frame sync -> script ----
    step("merging")
    with timings.step("merge_and_sync", "Build script (merge words, speakers, frames)") as span:
        turns = merge.build_turns(words, speaker_segments)
        segments = sync.attach_frames(turns, keyframes)
        span["meta"]["turns"] = len(segments)
    _save_list(out / "script.json", segments)
    (out / "script.md").write_text(summarize.render_script(segments))

    # ---- F6 classify + summarize ----
    if run_llm:
        llm = llm or make_llm(settings)  # Qwen or Claude, per LLM_PROVIDER
        llm_name = getattr(llm, "name", "LLM")
        step("classifying")
        log(f"[{vid}] classifying...")
        with timings.step("classify", f"Classify meeting / vlog ({llm_name})", kind="llm") as span:
            classification = summarize.classify(
                llm, segments, keyframes, out, info.duration, window_s=settings.classify_window_s)
            span["meta"].update(getattr(llm, "last_usage", None) or {}, result=classification.type)
        step("summarizing")
        log(f"[{vid}] type: {classification.type} ({classification.confidence}); summarizing...")
        with timings.step("summarize", f"Write {classification.type} summary ({llm_name})", kind="llm") as span:
            body = summarize.summarize(llm, classification.type, segments, keyframes, out,
                                       info.duration, effort=settings.llm_effort)
            span["meta"].update(getattr(llm, "last_usage", None) or {})
        result = VideoSummary(
            video_id=vid, date=date, duration=info.duration, classification=classification,
            vlog=body if classification.type == "vlog" else None,
            meeting=body if classification.type == "meeting" else None,
        )
        (out / "summary.json").write_text(result.model_dump_json(indent=2))
        with timings.step("render", "Render Markdown + daily journal"):
            render_outputs(out_root, vid, settings)

        # ---- Stage 2: add to the search index ----
        step("indexing")
        _index(out, settings, components, timings, log, vid)

    if cached:
        log(f"[{vid}] reused saved results for: {', '.join(cached)} (use --force to redo)")


def _index(out: Path, settings: Settings, components: Components, timings: Timings,
           log: Callable[[str], None], vid: str) -> None:
    from .memory.store import MemoryUnavailable

    try:
        with timings.step("index", "Add to search") as span:
            if components.index is None:
                from .memory.embeddings import get_embedder, is_loaded
                from .memory.indexer import check_database, index_output

                with timings.step("index.connect.db", "Check database connection", parent="index"):
                    check_database(settings)
                # first use in this process loads the model (cold); later videos reuse it (warm)
                cold = not is_loaded(settings.embed_model)
                with timings.step("index.load_model", f"Load embedding model ({'cold' if cold else 'warm'})",
                                  kind="load", parent="index") as load:
                    get_embedder(settings.embed_model)
                    load["meta"]["cold"] = cold
                n = index_output(out, settings, timings=timings)
            else:
                n = components.index(out, settings)
            span["meta"]["chunks"] = n
        if n is not None:
            log(f"[{vid}] indexed {n} chunks for search")
    except MemoryUnavailable as e:
        # the diary is still complete; search can be updated later with `lifestream index`
        log(f"[{vid}] search index not updated: {e}")


def render_outputs(out_root: str | Path, video_id: str, settings: Settings) -> None:
    """Write summary.md and the day's diary with current speaker names (F10).
    Called after summarizing and whenever names change."""
    out_root = Path(out_root)
    path = out_root / video_id / "summary.json"
    if not path.exists():
        return
    summary = VideoSummary.model_validate(json.loads(path.read_text()))
    registry = SpeakerRegistry(settings.speakers_file)
    named = apply_names(summary, registry.names_for(video_id))
    (out_root / video_id / "summary.md").write_text(summarize.render_summary(named))
    build_diary(out_root, summary.date, registry)


def build_diary(out_root: str | Path, date: str, registry: SpeakerRegistry | None = None) -> Path | None:
    """(Re)build the daily summary from every processed video with this date."""
    out_root = Path(out_root)
    videos = summarize.load_summaries(out_root, date)
    if not videos:
        return None
    if registry is not None:
        videos = [apply_names(v, registry.names_for(v.video_id)) for v in videos]
    path = out_root / "diaries" / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summarize.render_diary(date, videos))
    return path


def load_script(out_dir: str | Path) -> list[ScriptSegment]:
    return _load_list(Path(out_dir) / "script.json", ScriptSegment)
