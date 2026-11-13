"""REST API for the dashboard (local version of F12).

    POST   /api/videos                  upload_video  (multipart: file, date)
    GET    /api/videos                  list videos with status
    GET    /api/videos/{id}             everything the diary page needs
    GET    /api/videos/{id}/status      get_status
    GET    /api/videos/{id}/summary     get_summary
    GET    /api/videos/{id}/media       the video file (supports seeking)
    GET    /api/videos/{id}/keyframes/{name}
    POST   /api/videos/{id}/retry       process again (reuses finished steps)
    GET    /api/videos/{id}/speakers    speaker labels and names (F10)
    PUT    /api/videos/{id}/speakers    set names; re-renders the diary and re-indexes
    DELETE /api/videos/{id}
    GET    /api/days                    dates that have diaries
    GET    /api/days/{date}             daily summary
    POST   /api/query                   query_memory: answer a question with citations (F9)
    GET    /api/search/status           is the search database reachable, what's indexed
    GET    /api/videos/{id}/timeline    latency breakdown of the latest run (live while processing)
    GET    /api/latency/runs            run history with per-step timings
    GET    /api/latency/queries         recent Ask/Search queries with timings
    GET    /api/days/{date}/summary     same as /api/days/{date}
    GET    /api/config                  {mode: local | cloud, auth: bool} (no API key needed)

Speaker names from the registry are applied to everything returned.

Cloud mode (Stage 3, S3_BUCKET set): POST /api/videos takes JSON {date, filename} and returns
{video_id, presigned_put_url, upload_headers}; the browser uploads straight to S3, which starts
the GPU job (EventBridge -> Batch). POST /api/videos/{id}/uploaded marks the upload finished.
Processing status comes from S3 (cloud/sync.py); /media redirects to a temporary S3 link.
With API_KEY set, every /api route except /api/config needs it: X-API-Key header or the
dashboard's `lifestream_key` cookie (so <img>/<video> requests carry it too). Otherwise 401.
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, get_settings
from ..memory.registry import RegistryError, SpeakerRegistry, apply_names
from ..memory.store import MemoryConfigError, MemoryUnavailable
from ..processing.media import SUPPORTED_EXTENSIONS
from ..timing import STEP_CATEGORIES, Timings, aggregate, append_log, category_of, read_log, run_stats
from .jobs import STEP_LABELS, Job, JobQueue, ProcessFn, pre_processing_spans, read_status, write_status
from ..cloud.sync import CloudStore, CloudSync, live_spans

VIDEO_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
KEYFRAME = re.compile(r"^frame_\d+\.jpg$")
CHUNK = 1024 * 1024
KEY_COOKIE = "lifestream_key"
CLOUD_MAX_UPLOAD_BYTES = 5 * 1024**3  # one presigned PUT


class UploadIn(BaseModel):
    """Cloud upload request: the file itself goes straight to S3."""
    date: str
    filename: str
    size_bytes: int | None = None


class QueryIn(BaseModel):
    question: str


class SpeakersIn(BaseModel):
    names: dict[str, str | None]


AnswerFn = Callable[[str, Timings], dict]
IndexFn = Callable[[Path, Settings], "int | None"]
UnindexFn = Callable[[str, Settings], None]


def _default_answer(settings: Settings) -> AnswerFn:
    def answer(question: str, timings: Timings) -> dict:
        from ..intelligence.llm import make_llm
        from ..memory.answer import answer_question
        from ..memory.indexer import open_store

        from ..memory.embeddings import get_embedder, is_loaded

        cold = not is_loaded(settings.embed_model)
        with timings.step("connect.load_model", f"Load embedding model ({'cold' if cold else 'warm'})",
                          kind="load") as span:
            get_embedder(settings.embed_model)
            span["meta"]["cold"] = cold
        with timings.step("connect.db", "Connect to database"):
            store = open_store(settings)
        timings.meta["config"] = {"llm_model": settings.active_llm_model, "embed_model": settings.embed_model}
        with store:
            result = answer_question(question, store, make_llm(settings),
                                     dt.date.today(), settings.search_k, timings=timings)
        return result.model_dump(mode="json")
    return answer


def _default_index(out_dir: Path, settings: Settings, timings: Timings | None = None) -> int | None:
    """`timings`: record model loading, embedding and database time as sub-steps of "index"."""
    from ..memory.embeddings import get_embedder, is_loaded
    from ..memory.indexer import index_output
    if timings is not None:
        cold = not is_loaded(settings.embed_model)
        with timings.step("index.load_model", f"Load embedding model ({'cold' if cold else 'warm'})",
                          kind="load", parent="index") as span:
            get_embedder(settings.embed_model)
            span["meta"]["cold"] = cold
    return index_output(out_dir, settings, timings=timings)


def _default_unindex(video_id: str, settings: Settings) -> None:
    from ..memory.indexer import open_store
    with open_store(settings) as store:
        store.delete_video(video_id)


def _categorize(spans: list[dict]) -> list[dict]:
    """Attach each span's category so the dashboard colours it the same way the analysis counts it."""
    for span in spans:
        span["category"] = category_of(span)
    return spans


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def create_app(
    out_root: Path = Path("data/out"),
    uploads_dir: Path = Path("data/uploads"),
    settings: Settings | None = None,
    process_fn: ProcessFn | None = None,
    start_worker: bool = True,
    web_dist: Path | None = Path("web/dist"),
    max_upload_bytes: int = 1024**3,
    answer_fn: AnswerFn | None = None,
    index_fn: IndexFn | None = None,
    unindex_fn: UnindexFn | None = None,
    cloud: CloudStore | None = None,
) -> FastAPI:
    """`cloud`: S3/Batch access for cloud mode; by default created when S3_BUCKET is set."""
    settings = settings or get_settings()
    answer_fn = answer_fn or _default_answer(settings)
    index_fn = index_fn or _default_index
    unindex_fn = unindex_fn or _default_unindex
    out_root, uploads_dir = Path(out_root), Path(uploads_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    if cloud is None and settings.s3_bucket:
        cloud = CloudStore.from_settings(settings)
    jobs = CloudSync(out_root, settings, cloud, index_fn) if cloud else JobQueue(out_root, settings, process_fn)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="LifeStream", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.cloud = cloud

    if settings.api_key:
        expected = settings.api_key.encode()

        @app.middleware("http")
        async def require_api_key(request: Request, call_next):
            path = request.url.path
            if path.startswith("/api/") and path != "/api/config":
                given = request.headers.get("x-api-key") or request.cookies.get(KEY_COOKIE) or ""
                if not hmac.compare_digest(given.encode(), expected):
                    return JSONResponse({"detail": "Missing or wrong API key"}, status_code=401)
            return await call_next(request)

    @app.get("/api/config")
    def get_config():
        return {"mode": "cloud" if cloud else "local", "auth": bool(settings.api_key)}

    # ---------- helpers ----------
    def folder_for(video_id: str) -> Path:
        if not VIDEO_ID.match(video_id):
            raise HTTPException(404, "Video not found")
        folder = out_root / video_id
        if not (folder / "metadata.json").exists() and not (folder / "status.json").exists():
            raise HTTPException(404, "Video not found")
        return folder

    def names_for(video_id: str) -> dict[str, str]:
        try:
            return SpeakerRegistry(settings.speakers_file).names_for(video_id)
        except RegistryError:
            return {}

    def video_path(folder: Path) -> Path | None:
        status = read_status(folder)
        meta = _read_json(folder / "metadata.json") or {}
        raw = status.get("video_path") or meta.get("path")
        return Path(raw) if raw else None

    def item(folder: Path) -> dict:
        status = read_status(folder)
        meta = _read_json(folder / "metadata.json") or {}
        summary = _read_json(folder / "summary.json")
        body = (summary or {}).get("meeting") or (summary or {}).get("vlog") or {}
        keyframes = _read_json(folder / "keyframes.json") or []
        path = video_path(folder)
        name = status.get("original_name") or (path.name if path else folder.name)
        created = status.get("created_at")
        if created is None:
            meta_file = folder / "metadata.json"
            created = meta_file.stat().st_mtime if meta_file.exists() else time.time()
        state = status.get("status", "unknown")
        step = status.get("step")
        return {
            "video_id": folder.name,
            "title": apply_names(body.get("title"), names_for(folder.name)) if body.get("title") else Path(name).stem,
            "original_name": name,
            "date": (summary or {}).get("date") or meta.get("date") or status.get("date"),
            "duration": meta.get("duration"),
            "size_bytes": status.get("size_bytes") or (path.stat().st_size if path and path.exists() else None),
            "uploaded_at": created,
            "status": state,
            "step": step,
            "step_label": STEP_LABELS.get(step or "", "") if state != "failed" else "Failed",
            "progress": status.get("progress", 100 if state == "done" else 0),
            "error": status.get("error"),
            "type": ((summary or {}).get("classification") or {}).get("type"),
            "thumbnail": f"/api/videos/{folder.name}/keyframes/{Path(keyframes[0]['path']).name}"
            if keyframes else None,
        }

    def all_folders() -> list[Path]:
        return [f for f in out_root.iterdir()
                if f.is_dir() and VIDEO_ID.match(f.name)
                and ((f / "metadata.json").exists() or (f / "status.json").exists())]

    def speaker_stats(script: list[dict], names: dict[str, str]) -> list[dict]:
        """Talk time per speaker. `speaker` is the display name, `label` the raw label."""
        talk: dict[str, dict] = {}
        for s in script:
            label = s["speaker"]
            entry = talk.setdefault(label, {"label": label, "speaker": names.get(label, label),
                                            "name": names.get(label), "seconds": 0.0, "turns": 0,
                                            "first_seen": s["start"]})
            entry["seconds"] += s["end"] - s["start"]
            entry["turns"] += 1
        total = sum(e["seconds"] for e in talk.values()) or 1.0
        for e in talk.values():
            e["share"] = e["seconds"] / total
        return sorted(talk.values(), key=lambda e: -e["seconds"])

    # ---------- videos ----------
    @app.get("/api/videos")
    def list_videos():
        return sorted((item(f) for f in all_folders()), key=lambda v: -v["uploaded_at"])

    def check_upload(date: str, filename: str | None) -> tuple[str, str, str]:
        """Validate the date and file type; returns (file name, extension, new video ID)."""
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
        name = Path(filename or "video").name
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type '{ext or name}'. "
                                     f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        base = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:48].strip("-") or "video"
        video_id, n = base, 2
        while (out_root / video_id).exists():
            video_id, n = f"{base}-{n}", n + 1
        return name, ext, video_id

    if cloud:
        @app.post("/api/videos", status_code=201)
        def upload_video_cloud(body: UploadIn):
            """upload_video (cloud): returns a presigned S3 PUT link. The browser must send
            `upload_headers` with the PUT (they carry the recording date as S3 metadata)."""
            name, ext, video_id = check_upload(body.date, body.filename)
            if body.size_bytes is not None and not 0 < body.size_bytes <= CLOUD_MAX_UPLOAD_BYTES:
                raise HTTPException(413 if body.size_bytes else 400,
                                    "The file is empty" if not body.size_bytes else "File is larger than 5 GB")
            key = cloud.upload_key(video_id, ext)
            url, headers = cloud.presign_upload(key, body.date)
            now = time.time()
            write_status(out_root / video_id, status="queued", step="uploading", progress=0, error=None,
                         original_name=name, size_bytes=body.size_bytes, created_at=now, date=body.date,
                         s3_key=key, upload_started_at=now, uploaded_at=None, queued_at=None)
            return {**item(out_root / video_id), "presigned_put_url": url, "upload_headers": headers}

        @app.post("/api/videos/{video_id}/uploaded", status_code=202)
        def upload_finished(video_id: str):
            """The browser's PUT finished; the GPU job starts on its own (EventBridge)."""
            folder = folder_for(video_id)
            if read_status(folder).get("step") == "uploading":
                now = time.time()
                write_status(folder, step="queued", uploaded_at=now, queued_at=now)
            return item(folder)

    @app.post("/api/videos", status_code=202)
    def upload_video(file: UploadFile = File(...), date: str = Form(...),
                     client_started_at: float | None = Form(None)):
        """`client_started_at`: when the browser started sending (epoch ms), for the
        upload span of the latency timeline. The body is fully received before this runs."""
        received_at = time.time()
        name, ext, video_id = check_upload(date, file.filename)

        dest = uploads_dir / f"{video_id}{ext}"
        size = 0
        with dest.open("wb") as out:
            while chunk := file.file.read(CHUNK):
                size += len(chunk)
                if size > max_upload_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"File is larger than {max_upload_bytes // 1024**2} MB")
                out.write(chunk)
        if size == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "The file is empty")

        upload_started = client_started_at / 1000 if client_started_at else None
        if upload_started is not None and not (received_at - 6 * 3600 < upload_started <= received_at):
            upload_started = None  # ignore clocks that clearly disagree
        write_status(out_root / video_id, original_name=name, size_bytes=size, created_at=time.time(),
                     upload_started_at=upload_started, received_at=received_at, saved_at=time.time())
        jobs.submit(Job(video_id, dest.resolve(), date))
        return item(out_root / video_id)

    @app.get("/api/videos/{video_id}")
    def get_video(video_id: str):
        folder = folder_for(video_id)
        names = names_for(video_id)
        script = _read_json(folder / "script.json") or []
        keyframes = _read_json(folder / "keyframes.json") or []
        for k in keyframes:
            k["url"] = f"/api/videos/{video_id}/keyframes/{Path(k['path']).name}"
        return {
            **item(folder),
            "metadata": _read_json(folder / "metadata.json"),
            "summary": apply_names(_read_json(folder / "summary.json"), names),
            "script": [{**s, "speaker": names.get(s["speaker"], s["speaker"])} for s in script],
            "keyframes": keyframes,
            "speakers": speaker_stats(script, names),
            "timings": _read_json(folder / "timings.json"),
        }

    @app.get("/api/videos/{video_id}/status")
    def get_status(video_id: str):
        folder = folder_for(video_id)
        v = item(folder)
        timings = read_status(folder).get("step_timings") or _read_json(folder / "timings.json") or {}
        return {**{k: v[k] for k in ("video_id", "status", "step", "step_label", "progress", "error", "type")},
                "step_timings": timings}

    @app.get("/api/videos/{video_id}/summary")
    def get_summary(video_id: str):
        folder = folder_for(video_id)
        summary = _read_json(folder / "summary.json")
        if summary is None:
            raise HTTPException(404, "Summary not ready")
        md = folder / "summary.md"
        return {"summary": apply_names(summary, names_for(video_id)),
                "markdown": md.read_text() if md.exists() else None}

    @app.get("/api/videos/{video_id}/media")
    def get_media(video_id: str):
        folder = folder_for(video_id)
        key = read_status(folder).get("s3_key")
        if cloud and key:
            return RedirectResponse(cloud.presign_download(key), status_code=307)
        path = video_path(folder)
        if path is None or not path.exists():
            raise HTTPException(404, "Video file not found")
        return FileResponse(path)

    @app.get("/api/videos/{video_id}/keyframes/{name}")
    def get_keyframe(video_id: str, name: str):
        folder = folder_for(video_id)
        if not KEYFRAME.match(name) or not (folder / "keyframes" / name).exists():
            raise HTTPException(404, "Keyframe not found")
        return FileResponse(folder / "keyframes" / name, media_type="image/jpeg")

    @app.post("/api/videos/{video_id}/retry", status_code=202)
    def retry_video(video_id: str):
        folder = folder_for(video_id)
        status = read_status(folder)
        if status.get("status") in ("queued", "processing"):
            raise HTTPException(409, "This video is already being processed")
        date = item(folder)["date"]
        if cloud and status.get("s3_key"):
            if not status.get("uploaded_at") or not date:
                raise HTTPException(409, "The upload didn't finish; upload the video again")
            cloud.clear_worker_status(video_id)
            job_id = cloud.submit_job(video_id, status["s3_key"], date)
            write_status(folder, status="queued", step="queued", progress=0, error=None, job_id=job_id,
                         queued_at=time.time(), started_at=None, step_started_at=None, finished_at=None)
            return item(folder)
        path = video_path(folder)
        if path is None or not path.exists() or not date:
            raise HTTPException(409, "The original video file is missing; upload it again")
        jobs.submit(Job(video_id, path.resolve(), date))
        return item(folder)

    @app.get("/api/videos/{video_id}/timeline")
    def get_timeline(video_id: str):
        """Latest run's latency breakdown. While a video is queued or processing, the
        step in progress is added as a 'running' span that ends now."""
        folder = folder_for(video_id)
        status = read_status(folder)
        state = status.get("status")
        now = time.time()
        cloud_video = bool(cloud and status.get("s3_key"))
        if state == "queued":
            timeline = {"video_id": video_id, "status": "queued", "error": None, "finished_at": None,
                        "spans": live_spans(status, now) if cloud_video else pre_processing_spans(status, now)}
            for span in timeline["spans"]:
                if span["name"] == "queue":
                    span["running"] = True
        else:
            timeline = _read_json(folder / "timeline.json")
            tl_file = folder / "timeline.json"
            stale = (state == "processing" and status.get("started_at") and
                     (timeline is None or tl_file.stat().st_mtime < status["started_at"]))
            if stale or (cloud_video and timeline is None):  # this run's timeline isn't written yet
                spans = (live_spans(status, now) if cloud_video
                         else pre_processing_spans(status, status["started_at"]))
                timeline = {"video_id": video_id, "status": "running", "error": None, "finished_at": None,
                            "spans": spans}
            if timeline is None:
                raise HTTPException(404, "No timing recorded for this video (it was processed before "
                                         "latency tracking, or by an older version). Retry it to record one.")
            if state == "processing" and timeline.get("status") == "running" and status.get("step_started_at"):
                step = status.get("step") or ""
                timeline["spans"].append({
                    "name": f"running.{step}", "label": STEP_LABELS.get(step, step), "kind": "running",
                    "category": STEP_CATEGORIES.get(step, "other"),
                    "parent": None, "start": status["step_started_at"], "end": now,
                    "duration": round(now - status["step_started_at"], 3), "meta": {}, "running": True})
        spans = timeline["spans"]
        if spans:
            start = min(s["start"] for s in spans)
            end = timeline.get("finished_at") or max([s["end"] for s in spans] + [now if state in
                                                                                  ("queued", "processing") else 0])
            timeline["started_at"], timeline["total"] = start, round(end - start, 3)
        meta = _read_json(folder / "metadata.json") or {}
        timeline.setdefault("video_duration", meta.get("duration"))
        _categorize(spans)
        timeline["now"] = now
        timeline["stats"] = run_stats(timeline) if spans else None
        return timeline

    @app.get("/api/videos/{video_id}/speakers")
    def get_speakers(video_id: str):
        folder = folder_for(video_id)
        return speaker_stats(_read_json(folder / "script.json") or [], names_for(video_id))

    @app.put("/api/videos/{video_id}/speakers")
    def set_speakers(video_id: str, body: SpeakersIn):
        """F10: name speakers. Updates the registry, re-renders the summary and diary,
        and re-indexes the video so search finds the names."""
        folder = folder_for(video_id)
        script = _read_json(folder / "script.json") or []
        known = {s["speaker"] for s in script}
        unknown = sorted(set(body.names) - known)
        if unknown:
            raise HTTPException(400, f"Not a speaker in this video: {', '.join(unknown)}")
        try:
            SpeakerRegistry(settings.speakers_file).set_names(video_id, body.names)
        except RegistryError as e:
            raise HTTPException(400, str(e))
        from ..pipeline import render_outputs
        render_outputs(out_root, video_id, settings)
        indexed, index_error = False, None
        try:
            indexed = index_fn(folder, settings) is not None
        except (MemoryUnavailable, MemoryConfigError) as e:
            index_error = str(e)
        return {"speakers": speaker_stats(script, names_for(video_id)),
                "indexed": indexed, "index_error": index_error}

    @app.delete("/api/videos/{video_id}", status_code=204)
    def delete_video(video_id: str):
        folder = folder_for(video_id)
        status = read_status(folder)
        # an upload the browser never finished can be deleted (nothing is processing it)
        if status.get("status") in ("queued", "processing") and status.get("step") != "uploading":
            raise HTTPException(409, "Can't delete a video while it is being processed")
        if cloud and read_status(folder).get("s3_key"):
            cloud.delete_video(video_id)
        path = video_path(folder)
        # only delete files we own (uploads), never the user's original files elsewhere
        if path and path.exists() and path.resolve().parent == uploads_dir.resolve():
            path.unlink()
        date = item(folder)["date"]
        shutil.rmtree(folder)
        try:
            SpeakerRegistry(settings.speakers_file).remove_video(video_id)
        except RegistryError:
            pass
        try:
            unindex_fn(video_id, settings)
        except (MemoryUnavailable, MemoryConfigError):
            pass  # nothing to remove if search isn't running
        if date:
            from ..pipeline import build_diary
            if build_diary(out_root, date) is None:
                (out_root / "diaries" / f"{date}.md").unlink(missing_ok=True)

    # ---------- days ----------
    @app.get("/api/days")
    def list_days():
        counts: dict[str, int] = {}
        for f in all_folders():
            v = item(f)
            if v["status"] == "done" and v["date"]:
                counts[v["date"]] = counts.get(v["date"], 0) + 1
        return [{"date": d, "video_count": c} for d, c in sorted(counts.items(), reverse=True)]

    @app.get("/api/days/{date}")
    @app.get("/api/days/{date}/summary")
    def get_day(date: str):
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
        videos = []
        for f in all_folders():
            summary = _read_json(f / "summary.json")
            if summary and summary.get("date") == date:
                videos.append({**item(f), "summary": apply_names(summary, names_for(f.name))})
        if not videos:
            raise HTTPException(404, f"No diary for {date}")
        md = out_root / "diaries" / f"{date}.md"
        return {"date": date, "videos": sorted(videos, key=lambda v: v["uploaded_at"]),
                "markdown": md.read_text() if md.exists() else None}

    # ---------- search (Stage 2) ----------
    queries_log = out_root / "_logs" / "queries.jsonl"

    def log_query(question: str, timings: Timings, status: str, error: str | None, result: dict | None):
        tl = timings.finish(status, error)
        append_log(queries_log, {
            "at": tl["started_at"], "question": question, "status": status, "error": error,
            "found": (result or {}).get("found"), "citations": len((result or {}).get("citations", [])),
            "total": tl["total"], "spans": tl["spans"], "stats": run_stats(tl)})
        return tl

    @app.post("/api/query")
    def query_memory(body: QueryIn):
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Ask a question")
        if len(question) > 1000:
            raise HTTPException(400, "Question is too long (max 1000 characters)")
        from ..intelligence.llm import LLMError
        timings = Timings()
        try:
            result = answer_fn(question, timings)
        except (MemoryUnavailable, MemoryConfigError, LLMError) as e:
            log_query(question, timings, "failed", str(e), None)
            code = 503 if isinstance(e, MemoryUnavailable) else 409 if isinstance(e, MemoryConfigError) else 502
            raise HTTPException(code, str(e))
        tl = log_query(question, timings, "done", None, result)
        result["timing"] = {"total": tl["total"], "spans": _categorize(tl["spans"]),
                            "breakdown": run_stats(tl)["breakdown"]}
        # add what the dashboard needs to show each source
        for c in result.get("citations", []):
            folder = out_root / c["video_id"]
            c["thumbnail"] = item(folder)["thumbnail"] if folder.exists() else None
        return result

    @app.get("/api/latency/runs")
    def latency_runs(limit: int = 50):
        """Run history (newest first), each with its timeline and headline stats."""
        runs = []
        for record in read_log(out_root / "_logs" / "runs.jsonl", max(1, min(limit, 500))):
            vid = record.get("video_id", "")
            folder = out_root / vid
            exists = VIDEO_ID.match(vid) and folder.exists()
            _categorize(record.get("spans", []))
            runs.append({**record, "title": item(folder)["title"] if exists else record.get("file", vid),
                         "deleted": not exists, "stats": run_stats(record)})
        return runs

    @app.get("/api/latency/summary")
    def latency_summary(limit: int = 20, environment: str | None = None):
        """Headline numbers across the last `limit` runs of one environment (cloud GPU or this
        computer; default: cloud if it has runs) and the recent queries."""
        n = max(1, min(limit, 500))
        return aggregate(read_log(out_root / "_logs" / "runs.jsonl", n), read_log(queries_log, 200), environment)

    @app.get("/api/latency/queries")
    def latency_queries(limit: int = 50):
        records = read_log(queries_log, max(1, min(limit, 500)))
        for r in records:
            _categorize(r.get("spans", []))
            r["stats"] = run_stats(r)
        return records

    @app.get("/api/search/status")
    def search_status():
        from ..memory.indexer import open_store
        try:
            with open_store(settings) as store:
                counts = store.count()
            return {"available": True, **counts, "error": None}
        except (MemoryUnavailable, MemoryConfigError) as e:
            return {"available": False, "videos": 0, "chunks": 0, "error": str(e)}

    # ---------- built frontend (npm run build) ----------
    if web_dist is not None and (Path(web_dist) / "index.html").exists():
        dist = Path(web_dist)
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
