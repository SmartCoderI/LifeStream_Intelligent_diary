"""Runs Stage 1 end to end for one video.

    video -> F1 probe + audio -> [audio branch: F3 ASR, F2 diarization]
                              -> [visual branch: F4 scenes + keyframes]   (parallel)
          -> F3 merge into turns -> F5 attach frames -> F6 classify + summarize
          -> daily diary

Each step's output is saved in the video's folder. On a re-run, finished
steps are loaded from disk instead of recomputed (use force=True to redo).
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import re
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import TypeAdapter

from . import asr, diarize, media, merge, scenes, summarize, sync
from .config import Settings
from .llm import AnthropicLLM, LLMClient
from .schemas import Keyframe, Scene, ScriptSegment, SpeakerSegment, VideoSummary, Word
from .timing import Timings

WordsFn = Callable[[Path, Settings], list[Word]]
DiarizeFn = Callable[..., list[SpeakerSegment]]
VisualFn = Callable[..., tuple[list[Scene], list[Keyframe]]]


@dataclass
class Components:
    """The heavy steps, swappable for fakes in tests."""

    transcribe: WordsFn = asr.transcribe
    diarize: DiarizeFn = diarize.diarize
    visual: VisualFn = scenes.run_visual_branch


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
    start = time.perf_counter()
    result = fn(**kwargs)
    return result, time.perf_counter() - start


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
) -> Path:
    """Process one video. Returns its output folder.

    `on_step(name)` is called as each step starts (used by the dashboard for progress).
    """
    components = components or Components()
    step = on_step or (lambda name: None)
    timings = Timings()
    t0 = time.perf_counter()

    video = Path(video)
    out_root = Path(out_root)
    vid = video_id or video_id_for(video, out_root)
    out = out_root / vid
    out.mkdir(parents=True, exist_ok=True)
    cached: list[str] = []

    # ---- F1: validate + metadata + audio ----
    step("validating")
    with timings.step("probe"):
        info = media.probe(video)
    (out / "metadata.json").write_text(json.dumps(
        {"video_id": vid, "date": date, **info.model_dump()}, indent=2))
    log(f"[{vid}] {info.duration / 60:.1f} min, {info.width}x{info.height}, {info.fps:.2f} fps")

    wav = out / "audio.wav"
    if wav.exists() and not force:
        cached.append("extract_audio")
    else:
        step("extracting_audio")
        with timings.step("extract_audio"):
            media.extract_audio(video, wav)

    # ---- F4 visual branch, started in parallel with the audio branch ----
    scenes_path, frames_path = out / "scenes.json", out / "keyframes.json"
    visual_kwargs = dict(
        video=str(video), out_dir=str(out), duration=info.duration,
        threshold=settings.scene_threshold, min_scene_len_s=settings.min_scene_len_s,
        max_keyframes=settings.max_keyframes, keyframe_width=settings.keyframe_width,
    )
    visual_cached = scenes_path.exists() and frames_path.exists() and not force
    pool = None
    visual_future = None
    visual_saved = visual_cached
    if visual_cached:
        cached.append("visual_branch")
    elif parallel:
        # separate process: true parallelism, and keeps OpenCV out of the ASR process
        pool = ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn"))
        visual_future = pool.submit(_timed_visual, components.visual, **visual_kwargs)
        log(f"[{vid}] scene detection started in parallel")

    try:
        # ---- audio branch: F3 ASR, then F2 diarization ----
        audio_start = time.perf_counter()
        words_path = out / "words.json"
        if words_path.exists() and not force:
            words = _load_list(words_path, Word)
            cached.append("transcribe")
        else:
            step("transcribing")
            log(f"[{vid}] transcribing ({settings.whisper_model}, {settings.device})...")
            with timings.step("transcribe"):
                words = components.transcribe(wav, settings)
            _save_list(words_path, words)

        diar_path = out / "diarization.json"
        if diar_path.exists() and not force:
            speaker_segments = _load_list(diar_path, SpeakerSegment)
            cached.append("diarize")
        else:
            step("diarizing")
            log(f"[{vid}] diarizing...")
            with timings.step("diarize"):
                speaker_segments = components.diarize(
                    wav, settings, num_speakers=num_speakers,
                    min_speakers=min_speakers, max_speakers=max_speakers)
            _save_list(diar_path, speaker_segments)
        timings.record("audio_branch", time.perf_counter() - audio_start)

        # ---- wait for the visual branch ----
        step("detecting_scenes")
        if visual_cached:
            scene_list = _load_list(scenes_path, Scene)
            keyframes = _load_list(frames_path, Keyframe)
        else:
            if visual_future is not None:
                (scene_list, keyframes), visual_s = visual_future.result()
            else:
                (scene_list, keyframes), visual_s = _timed_visual(components.visual, **visual_kwargs)
            timings.record("visual_branch", visual_s)
            _save_list(scenes_path, scene_list)
            _save_list(frames_path, keyframes)
            visual_saved = True
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
            # if the audio branch failed, still keep the finished visual results
            if not visual_saved and visual_future.done() and visual_future.exception() is None:
                (v_scenes, v_frames), _ = visual_future.result()
                _save_list(scenes_path, v_scenes)
                _save_list(frames_path, v_frames)
    log(f"[{vid}] {len(words)} words, {len({s.speaker for s in speaker_segments})} speakers, "
        f"{len(scene_list)} scenes, {len(keyframes)} keyframes")

    # ---- F3 merge + F5 frame sync -> script ----
    step("merging")
    with timings.step("merge_and_sync"):
        turns = merge.build_turns(words, speaker_segments)
        segments = sync.attach_frames(turns, keyframes)
    _save_list(out / "script.json", segments)
    (out / "script.md").write_text(summarize.render_script(segments))

    # ---- F6 classify + summarize ----
    if run_llm:
        llm = llm or AnthropicLLM(settings.llm_model)
        step("classifying")
        log(f"[{vid}] classifying...")
        with timings.step("classify"):
            classification = summarize.classify(
                llm, segments, keyframes, out, info.duration, window_s=settings.classify_window_s)
        step("summarizing")
        log(f"[{vid}] type: {classification.type} ({classification.confidence}); summarizing...")
        with timings.step("summarize"):
            body = summarize.summarize(llm, classification.type, segments, keyframes, out,
                                       info.duration, effort=settings.llm_effort)
        result = VideoSummary(
            video_id=vid, date=date, duration=info.duration, classification=classification,
            vlog=body if classification.type == "vlog" else None,
            meeting=body if classification.type == "meeting" else None,
        )
        (out / "summary.json").write_text(result.model_dump_json(indent=2))
        (out / "summary.md").write_text(summarize.render_summary(result))
        build_diary(out_root, date)

    timings.save(out / "timings.json", total=time.perf_counter() - t0)
    if cached:
        log(f"[{vid}] reused saved results for: {', '.join(cached)} (use --force to redo)")
    return out


def build_diary(out_root: str | Path, date: str) -> Path | None:
    """(Re)build the daily summary from every processed video with this date."""
    out_root = Path(out_root)
    videos = summarize.load_summaries(out_root, date)
    if not videos:
        return None
    path = out_root / "diaries" / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summarize.render_diary(date, videos))
    return path


def load_script(out_dir: str | Path) -> list[ScriptSegment]:
    return _load_list(Path(out_dir) / "script.json", ScriptSegment)
