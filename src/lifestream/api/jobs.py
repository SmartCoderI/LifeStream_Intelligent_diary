"""Background processing for uploaded videos.

One worker thread processes videos one at a time (the models need most of a
laptop's CPU and memory). Each video's status lives in
data/out/<video_id>/status.json, so it survives a server restart.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings

# Pipeline steps in order, with the progress (%) shown when each one starts.
STEP_PROGRESS = {
    "queued": 0,
    "validating": 2,
    "extracting_audio": 5,
    "transcribing": 10,
    "diarizing": 45,
    "detecting_scenes": 75,
    "merging": 80,
    "classifying": 85,
    "summarizing": 90,
    "done": 100,
}
STEP_LABELS = {
    "queued": "Waiting in queue",
    "validating": "Checking the file",
    "extracting_audio": "Extracting audio",
    "transcribing": "Transcribing speech",
    "diarizing": "Identifying speakers",
    "detecting_scenes": "Detecting scenes",
    "merging": "Building the script",
    "classifying": "Classifying video",
    "summarizing": "Writing the diary",
    "done": "Completed",
    "failed": "Failed",
}


def read_status(folder: Path) -> dict:
    path = folder / "status.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    # videos processed with the CLI have no status file
    if (folder / "summary.json").exists():
        return {"status": "done", "step": "done", "progress": 100}
    return {"status": "failed", "step": "failed", "progress": 0,
            "error": "Processing didn't finish. Retry to continue from where it stopped."}


def write_status(folder: Path, **fields) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    data = read_status(folder) if (folder / "status.json").exists() else {}
    data.update(fields)
    data["updated_at"] = time.time()
    tmp = folder / "status.json.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(folder / "status.json")
    return data


@dataclass
class Job:
    video_id: str
    video_path: Path
    date: str


ProcessFn = Callable[..., Path]


class JobQueue:
    def __init__(self, out_root: Path, settings: Settings, process_fn: ProcessFn | None = None):
        from ..pipeline import process_video

        self.out_root = out_root
        self.settings = settings
        self.process_fn = process_fn or process_video
        self._queue: queue.Queue[Job | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="lifestream-worker", daemon=True)
            self._thread.start()
            self._requeue_unfinished()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=5)
            self._thread = None

    def submit(self, job: Job) -> None:
        write_status(self.out_root / job.video_id, status="queued", step="queued",
                     progress=0, error=None, video_path=str(job.video_path), date=job.date)
        self._queue.put(job)

    def run_now(self, job: Job) -> None:
        """Process in the calling thread (used by tests)."""
        write_status(self.out_root / job.video_id, status="queued", step="queued",
                     progress=0, error=None, video_path=str(job.video_path), date=job.date)
        self._run(job)

    def _requeue_unfinished(self) -> None:
        """After a restart, continue videos that were queued or mid-processing.
        Finished steps are saved, so they resume where they stopped."""
        for status_file in sorted(self.out_root.glob("*/status.json")):
            data = read_status(status_file.parent)
            if data.get("status") in ("queued", "processing") and data.get("video_path"):
                self.submit(Job(status_file.parent.name, Path(data["video_path"]), data["date"]))

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            self._run(job)

    def _run(self, job: Job) -> None:
        folder = self.out_root / job.video_id

        def on_step(name: str) -> None:
            write_status(folder, status="processing", step=name, progress=STEP_PROGRESS.get(name, 0))

        try:
            write_status(folder, status="processing", step="validating", progress=2, started_at=time.time())
            self.process_fn(job.video_path, job.date, self.out_root, self.settings,
                            video_id=job.video_id, on_step=on_step, log=lambda m: None)
            write_status(folder, status="done", step="done", progress=100, finished_at=time.time())
        except Exception as e:  # report any failure to the dashboard
            write_status(folder, status="failed", step="failed", error=str(e) or type(e).__name__,
                         finished_at=time.time())
            traceback.print_exc()
