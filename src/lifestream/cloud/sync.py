"""Stage 3 cloud mode, server side: the dashboard server's view of S3 and AWS Batch.

    browser --PUT (presigned)--> s3://<bucket>/uploads/<video_id>/<video_id>.<ext>
          --EventBridge rule--> Batch GPU job (cloud/worker.py)
          --> s3://<bucket>/out/<video_id>/...  plus out/<video_id>/status.json after every step

`CloudSync` replaces the local job queue: a background thread copies each active video's worker
status into its local status.json. When the worker reports `done`, it downloads the results into
the usual data/out/<video_id>/ folder, rebuilds the daily diary and indexes the video for search,
so every other dashboard feature works exactly as it does locally.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..api.jobs import STEP_PROGRESS, read_status, write_status
from ..config import Settings
from ..timing import append_log
from .worker import STATUS_FILE

ACTIVE = ("queued", "processing")
UPLOAD_URL_SECONDS = 3600          # time to finish a browser upload (a single PUT, up to 5 GB)
MEDIA_URL_SECONDS = 6 * 3600       # time to watch a video before the player's link expires

IndexFn = Callable[[Path, Settings], "int | None"]


@dataclass
class CloudStore:
    """S3 and Batch calls the dashboard server makes. Clients are injectable for tests."""

    bucket: str
    s3: object
    batch: object | None = None
    job_queue: str = "lifestream-gpu"
    job_definition: str = "lifestream-worker"

    @classmethod
    def from_settings(cls, settings: Settings) -> "CloudStore":
        import boto3
        from botocore.config import Config

        session = boto3.Session(region_name=settings.aws_region)
        s3 = session.client("s3", config=Config(signature_version="s3v4",
                                                s3={"addressing_style": "virtual"}))
        return cls(settings.s3_bucket, s3, session.client("batch"),
                   settings.batch_job_queue, settings.batch_job_definition)

    # ---------- keys ----------
    @staticmethod
    def upload_key(video_id: str, ext: str) -> str:
        """Only safe characters, so the key is the same in S3 events, URIs and job parameters."""
        return f"uploads/{video_id}/{video_id}{ext.lower()}"

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    # ---------- uploads and playback ----------
    def presign_upload(self, key: str, date: str) -> tuple[str, dict[str, str]]:
        """A PUT link for the browser. The date travels as object metadata (the worker reads it),
        so the browser must send the returned headers with the upload."""
        url = self.s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "Metadata": {"date": date}},
            ExpiresIn=UPLOAD_URL_SECONDS, HttpMethod="PUT")
        return url, {"x-amz-meta-date": date}

    def presign_download(self, key: str) -> str:
        return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                              ExpiresIn=MEDIA_URL_SECONDS)

    def uploaded_at(self, key: str) -> float | None:
        """When the upload finished (None while it's still uploading)."""
        try:
            return self.s3.head_object(Bucket=self.bucket, Key=key)["LastModified"].timestamp()
        except Exception as e:  # noqa: BLE001 - 404 until the PUT completes
            if _is_missing(e):
                return None
            raise

    # ---------- worker results ----------
    def worker_status(self, video_id: str) -> dict | None:
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=f"out/{video_id}/{STATUS_FILE}")["Body"]
            return json.loads(body.read())
        except Exception as e:  # noqa: BLE001
            if _is_missing(e):
                return None
            raise

    def download_results(self, video_id: str, folder: Path) -> int:
        """Copy out/<video_id>/* into folder (except the worker's status file). Returns the count."""
        prefix = f"out/{video_id}/"
        count = 0
        for key in self._keys(prefix):
            rel = key[len(prefix):]
            if not rel or rel == STATUS_FILE or ".." in rel.split("/"):
                continue
            dest = folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.s3.download_file(self.bucket, key, str(dest))
            count += 1
        return count

    def delete_video(self, video_id: str) -> None:
        keys = self._keys(f"uploads/{video_id}/") + self._keys(f"out/{video_id}/")
        for i in range(0, len(keys), 1000):
            self.s3.delete_objects(Bucket=self.bucket,
                                   Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})

    def clear_worker_status(self, video_id: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=f"out/{video_id}/{STATUS_FILE}")

    def _keys(self, prefix: str) -> list[str]:
        keys = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return keys

    # ---------- Batch ----------
    def submit_job(self, video_id: str, key: str, date: str) -> str:
        """Start a job by hand (Retry). New uploads start theirs through EventBridge."""
        job = self.batch.submit_job(jobName=f"retry-{video_id}"[:128], jobQueue=self.job_queue,
                                    jobDefinition=self.job_definition,
                                    parameters={"input": self.uri(key), "date": date})
        return job["jobId"]

    def batch_jobs(self, since: float) -> dict[str, dict]:
        """Jobs created after `since` (epoch s), newest per input URI: {uri: {status, reason, job_id}}."""
        ids, token = [], None
        while True:
            kwargs = {"jobQueue": self.job_queue,
                      "filters": [{"name": "AFTER_CREATED_AT", "values": [str(int(since * 1000))]}]}
            if token:
                kwargs["nextToken"] = token
            page = self.batch.list_jobs(**kwargs)
            ids += [j["jobId"] for j in page.get("jobSummaryList", [])]
            token = page.get("nextToken")
            if not token:
                break
        found: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            for job in self.batch.describe_jobs(jobs=ids[i:i + 100])["jobs"]:
                uri = (job.get("parameters") or {}).get("input")
                if uri and job.get("createdAt", 0) >= found.get(uri, {}).get("created_at", -1):
                    found[uri] = {"status": job["status"], "reason": job.get("statusReason"),
                                  "job_id": job["jobId"], "created_at": job.get("createdAt", 0)}
        return found


def _is_missing(error: Exception) -> bool:
    code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


class CloudSync:
    """Background thread: follows every active cloud video until it is done (or failed).
    Same start/stop interface as the local JobQueue."""

    def __init__(self, out_root: Path, settings: Settings, store: CloudStore, index_fn: IndexFn,
                 interval: float = 5.0, log: Callable[[str], None] = print):
        self.out_root, self.settings, self.store = Path(out_root), settings, store
        self.index_fn, self.interval, self.log = index_fn, interval, log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="lifestream-cloud-sync", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=10)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - keep following videos after a network hiccup
                traceback.print_exc()
            self._stop.wait(self.interval)

    # ---------- one pass ----------
    def tick(self) -> None:
        active = [f for f in sorted(self.out_root.iterdir()) if f.is_dir()
                  and (s := read_status(f)).get("status") in ACTIVE and s.get("s3_key")] \
            if self.out_root.exists() else []
        jobs: dict[str, dict] | None = None

        def batch_jobs() -> dict[str, dict]:
            nonlocal jobs
            if jobs is None:
                since = min(read_status(f).get("created_at") or time.time() for f in active) - 600
                try:
                    jobs = self.store.batch_jobs(since) if self.store.batch is not None else {}
                except Exception as e:  # noqa: BLE001 - informational; the worker status is the source of truth
                    self.log(f"Couldn't list Batch jobs: {e}")
                    jobs = {}
            return jobs

        for folder in active:
            try:
                self.refresh(folder, batch_jobs)
            except Exception as e:  # noqa: BLE001
                self.log(f"[{folder.name}] sync failed: {e}")

    def refresh(self, folder: Path, batch_jobs: Callable[[], dict[str, dict]]) -> None:
        local = read_status(folder)
        vid = folder.name
        remote = self.store.worker_status(vid)
        if remote is not None:
            state = remote.get("status")
            if state == "processing":
                write_status(folder, status="processing", step=remote.get("step"),
                             progress=remote.get("progress", STEP_PROGRESS.get(remote.get("step"), 0)),
                             started_at=remote.get("started_at"), step_started_at=remote.get("step_started_at"),
                             step_timings=remote.get("step_timings", {}), job_id=remote.get("job_id"),
                             error=None)
            elif state == "failed":
                write_status(folder, status="failed", step="failed", error=remote.get("error") or "Processing failed",
                             finished_at=remote.get("finished_at"), step_timings=remote.get("step_timings", {}))
            elif state == "done":
                self.finish(folder, local, remote)
            return

        # no word from the worker yet: is the upload finished, did the job fail before starting?
        if local.get("step") == "uploading":
            finished = self.store.uploaded_at(local["s3_key"])
            if finished is None:
                if time.time() - (local.get("created_at") or time.time()) > UPLOAD_URL_SECONDS + 600:
                    write_status(folder, status="failed", step="failed", finished_at=time.time(),
                                 error="The upload didn't finish. Delete this video and upload it again.")
                return
            write_status(folder, step="queued", progress=0, uploaded_at=local.get("uploaded_at") or finished,
                         queued_at=local.get("queued_at") or finished)
        job = batch_jobs().get(self.store.uri(local["s3_key"]))
        if job and job["status"] == "FAILED":
            write_status(folder, status="failed", step="failed", job_id=job["job_id"], finished_at=time.time(),
                         error=f"The GPU job failed before processing started: {job['reason'] or 'unknown reason'}")
        elif job and job["status"] == "SUCCEEDED":  # finished without ever writing status.json
            write_status(folder, status="failed", step="failed", job_id=job["job_id"], finished_at=time.time(),
                         error="The GPU job finished without reporting progress. Is the worker image "
                               "older than this server? Rebuild it: infra/deploy.sh --worker <new tag>")
        elif job:
            write_status(folder, job_id=job["job_id"], batch_status=job["status"])

    def finish(self, folder: Path, local: dict, remote: dict) -> None:
        """Worker done: download results, rebuild the diary, index for search, record the run."""
        from ..memory.store import MemoryConfigError, MemoryUnavailable
        from ..pipeline import render_outputs

        vid = folder.name
        step_timings = dict(remote.get("step_timings") or {})
        write_status(folder, status="processing", step="indexing", progress=STEP_PROGRESS["indexing"],
                     step_started_at=time.time(), step_timings=step_timings)
        try:
            self.store.download_results(vid, folder)
            render_outputs(self.out_root, vid, self.settings)
        except Exception as e:  # noqa: BLE001
            write_status(folder, status="failed", step="failed", finished_at=time.time(),
                         error=f"Couldn't load the results from S3: {e}")
            return
        t0 = time.time()
        index_error = None
        try:
            self.index_fn(folder, self.settings)
        except (MemoryUnavailable, MemoryConfigError) as e:
            index_error = str(e)  # the diary is complete; search can be rebuilt with `lifestream index`
        t1 = time.time()
        step_timings["indexing"] = round(t1 - t0, 2)
        self._record_run(folder, local, remote, t0, t1, index_error)
        write_status(folder, status="done", step="done", progress=100, finished_at=t1,
                     step_timings=step_timings, index_error=index_error, error=None)
        self.log(f"[{vid}] done ({len(step_timings)} steps)")

    def _record_run(self, folder: Path, local: dict, remote: dict, t0: float, t1: float,
                    index_error: str | None) -> None:
        """The worker's timeline plus what happened around it: browser upload, waiting for a GPU
        machine (cold start), and indexing on this server. Saved and added to the run history."""
        from ..pipeline import runs_log_path

        path = folder / "timeline.json"
        try:
            timeline = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            timeline = {"video_id": folder.name, "spans": []}
        spans = [s for s in timeline.get("spans", []) if s.get("name") not in ("upload", "queue", "index")]
        started, uploaded, worker_start = (local.get("upload_started_at"), local.get("uploaded_at"),
                                           remote.get("started_at"))
        if started and uploaded and uploaded >= started:
            spans.append(_span("upload", "Upload (browser to S3)", started, uploaded,
                               {"bytes": local.get("size_bytes")}))
        if uploaded and worker_start and worker_start >= uploaded:
            spans.append(_span("queue", "Waiting for a GPU machine (Batch start + image pull)",
                               uploaded, worker_start, {"job_id": remote.get("job_id")}))
        spans.append({**_span("index", "Add to search (dashboard server)", t0, t1,
                              {"error": index_error} if index_error else {}), "kind": "step"})
        spans.sort(key=lambda s: s["start"])
        start = min(s["start"] for s in spans)
        timeline.update(spans=spans, status="done", error=None, started_at=start, finished_at=t1,
                        total=round(t1 - start, 3), source="cloud")
        path.write_text(json.dumps(timeline, indent=2))
        append_log(runs_log_path(self.out_root), timeline)


def _span(name: str, label: str, start: float, end: float, meta: dict | None = None) -> dict:
    return {"name": name, "label": label, "kind": "io", "parent": None, "start": start, "end": end,
            "duration": round(end - start, 3), "meta": meta or {}}
