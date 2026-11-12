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

Speaker names from the registry are applied to everything returned.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings, get_settings
from ..memory.registry import RegistryError, SpeakerRegistry, apply_names
from ..memory.store import MemoryConfigError, MemoryUnavailable
from ..processing.media import SUPPORTED_EXTENSIONS
from .jobs import STEP_LABELS, Job, JobQueue, ProcessFn, read_status, write_status

VIDEO_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
KEYFRAME = re.compile(r"^frame_\d+\.jpg$")
CHUNK = 1024 * 1024


class QueryIn(BaseModel):
    question: str


class SpeakersIn(BaseModel):
    names: dict[str, str | None]


AnswerFn = Callable[[str], dict]
IndexFn = Callable[[Path, Settings], "int | None"]
UnindexFn = Callable[[str, Settings], None]


def _default_answer(settings: Settings) -> AnswerFn:
    def answer(question: str) -> dict:
        from ..intelligence.llm import AnthropicLLM
        from ..memory.answer import answer_question
        from ..memory.indexer import open_store

        with open_store(settings) as store:
            result = answer_question(question, store, AnthropicLLM(settings.llm_model),
                                     dt.date.today(), settings.search_k)
        return result.model_dump(mode="json")
    return answer


def _default_index(out_dir: Path, settings: Settings) -> int | None:
    from ..memory.indexer import index_output
    return index_output(out_dir, settings)


def _default_unindex(video_id: str, settings: Settings) -> None:
    from ..memory.indexer import open_store
    with open_store(settings) as store:
        store.delete_video(video_id)


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
) -> FastAPI:
    settings = settings or get_settings()
    answer_fn = answer_fn or _default_answer(settings)
    index_fn = index_fn or _default_index
    unindex_fn = unindex_fn or _default_unindex
    out_root, uploads_dir = Path(out_root), Path(uploads_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    jobs = JobQueue(out_root, settings, process_fn)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="LifeStream", lifespan=lifespan)
    app.state.jobs = jobs

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

    @app.post("/api/videos", status_code=202)
    def upload_video(file: UploadFile = File(...), date: str = Form(...)):
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
        name = Path(file.filename or "video").name
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type '{ext or name}'. "
                                     f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

        base = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:48].strip("-") or "video"
        video_id, n = base, 2
        while (out_root / video_id).exists():
            video_id, n = f"{base}-{n}", n + 1

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

        write_status(out_root / video_id, original_name=name, size_bytes=size, created_at=time.time())
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
        v = item(folder_for(video_id))
        return {k: v[k] for k in ("video_id", "status", "step", "step_label", "progress", "error")}

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
        path = video_path(folder_for(video_id))
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
        if read_status(folder).get("status") in ("queued", "processing"):
            raise HTTPException(409, "This video is already being processed")
        path = video_path(folder)
        date = item(folder)["date"]
        if path is None or not path.exists() or not date:
            raise HTTPException(409, "The original video file is missing; upload it again")
        jobs.submit(Job(video_id, path.resolve(), date))
        return item(folder)

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
        state = read_status(folder).get("status")
        if state in ("queued", "processing"):
            raise HTTPException(409, "Can't delete a video while it is being processed")
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
    @app.post("/api/query")
    def query_memory(body: QueryIn):
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Ask a question")
        if len(question) > 1000:
            raise HTTPException(400, "Question is too long (max 1000 characters)")
        from ..intelligence.llm import LLMError
        try:
            result = answer_fn(question)
        except MemoryUnavailable as e:
            raise HTTPException(503, str(e))
        except MemoryConfigError as e:
            raise HTTPException(409, str(e))
        except LLMError as e:
            raise HTTPException(502, str(e))
        # add what the dashboard needs to show each source
        for c in result.get("citations", []):
            folder = out_root / c["video_id"]
            c["thumbnail"] = item(folder)["thumbnail"] if folder.exists() else None
        return result

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
