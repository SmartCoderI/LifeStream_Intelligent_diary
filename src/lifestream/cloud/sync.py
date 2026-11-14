"""Stage 3 cloud mode, server side: the dashboard server's view of S3 and AWS Batch.

    browser --PUT (presigned; large files in parallel parts)--> s3://<bucket>/uploads/<video_id>/<video_id>.<ext>
          --EventBridge rule--> Batch GPU job (cloud/worker.py)
          --> s3://<bucket>/out/<video_id>/...  plus out/<video_id>/status.json after every step

`CloudSync` replaces the local job queue: a background thread copies each active video's worker
status into its local status.json. When the worker reports `done`, it downloads the results into
the usual data/out/<video_id>/ folder, rebuilds the daily diary and indexes the video for search,
so every other dashboard feature works exactly as it does locally.
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..api.jobs import STEP_LABELS, STEP_PROGRESS, read_status, write_status
from ..config import Settings
from ..timing import STEP_CATEGORIES, append_log
from .worker import STATUS_FILE

ACTIVE = ("queued", "processing")
TERMINAL_JOB_STATES = ("SUCCEEDED", "FAILED")
REAP_SECONDS = 15 * 60  # after a cancel, keep stopping jobs that show up late (EventBridge, retries)
UPLOAD_URL_SECONDS = 3600          # time to finish a browser upload (up to 5 GB)
# Large uploads go up in parts, several at a time: one connection rarely uses the whole uplink.
MULTIPART_PART_BYTES = 16 * 1024**2  # S3 needs >= 5 MiB per part (except the last), <= 10,000 parts
MULTIPART_MIN_BYTES = 2 * MULTIPART_PART_BYTES  # smaller files: one PUT
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

    def start_multipart(self, key: str, date: str, size_bytes: int) -> dict:
        """A parallel upload: one presigned PUT link per part. The date is set here, so the parts
        need no headers. The browser sends back each part's ETag for complete_multipart."""
        upload_id = self.s3.create_multipart_upload(Bucket=self.bucket, Key=key, Metadata={"date": date})["UploadId"]
        part_size = max(MULTIPART_PART_BYTES, -(-size_bytes // 10_000))
        count = -(-size_bytes // part_size)
        urls = [self.s3.generate_presigned_url(
                    "upload_part", Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": n},
                    ExpiresIn=UPLOAD_URL_SECONDS, HttpMethod="PUT")
                for n in range(1, count + 1)]
        return {"upload_id": upload_id, "part_size": part_size, "part_urls": urls}

    def complete_multipart(self, key: str, upload_id: str, parts: list[tuple[int, str]]) -> None:
        """parts: (part number, ETag) for every part. The object (and its S3 event) appears now."""
        self.s3.complete_multipart_upload(
            Bucket=self.bucket, Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": n, "ETag": etag} for n, etag in sorted(parts)]})

    def abort_multipart(self, key: str, upload_id: str) -> None:
        """Discard an unfinished parallel upload (the bucket lifecycle also does this after a day)."""
        try:
            self.s3.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except Exception as e:  # noqa: BLE001 - already completed or aborted
            if not _is_missing(e) and "NoSuchUpload" not in str(e):
                raise

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

    def download_file(self, key: str, dest: Path) -> None:
        self.s3.download_file(self.bucket, key, str(dest))

    def delete_object(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def terminate_job(self, job_id: str, reason: str) -> None:
        """Stops a Batch job in any state (waiting, starting or running)."""
        self.batch.terminate_job(jobId=job_id, reason=reason)

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
        self._lock = threading.Lock()  # a cancel and a sync never write one video's status at once

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
        folders = [f for f in sorted(self.out_root.iterdir()) if f.is_dir()] if self.out_root.exists() else []
        active, cancelled = [], []
        for f in folders:
            s = read_status(f)
            if not s.get("s3_key"):
                continue
            if s.get("status") in ACTIVE:
                active.append(f)
            elif s.get("status") == "cancelled" and time.time() - (s.get("cancelled_at") or 0) < REAP_SECONDS:
                cancelled.append(f)
        jobs: dict[str, dict] | None = None

        def batch_jobs() -> dict[str, dict]:
            nonlocal jobs
            if jobs is None:
                since = min(read_status(f).get("created_at") or time.time() for f in active + cancelled) - 600
                try:
                    jobs = self.store.batch_jobs(since) if self.store.batch is not None else {}
                except Exception as e:  # noqa: BLE001 - informational; the worker status is the source of truth
                    self.log(f"Couldn't list Batch jobs: {e}")
                    jobs = {}
            return jobs

        for folder in active:
            try:
                with self._lock:
                    if read_status(folder).get("status") in ACTIVE:  # not cancelled meanwhile
                        self.refresh(folder, batch_jobs)
            except Exception as e:  # noqa: BLE001
                self.log(f"[{folder.name}] sync failed: {e}")
        for folder in cancelled:
            try:
                self._stop_jobs(folder, batch_jobs)
            except Exception as e:  # noqa: BLE001
                self.log(f"[{folder.name}] couldn't stop its job: {e}")

    # ---------- cancel ----------
    def cancel(self, video_id: str) -> None:
        """Stop a cloud video wherever it is: delete an upload that may have landed, and terminate
        its Batch job (waiting or running). Jobs that show up after this (an upload finishing at the
        same moment) are stopped by the next syncs for REAP_SECONDS."""
        folder = self.out_root / video_id
        with self._lock:
            local = read_status(folder)
            now = time.time()
            write_status(folder, status="cancelled", step="cancelled", error=None, cancelled_at=now,
                         finished_at=now)
        if local.get("step") == "uploading" and local.get("s3_key"):
            try:
                if local.get("multipart_upload_id"):
                    self.store.abort_multipart(local["s3_key"], local["multipart_upload_id"])
                self.store.delete_object(local["s3_key"])
            except Exception as e:  # noqa: BLE001 - the GPU job then fails at download
                self.log(f"[{video_id}] couldn't delete the partial upload: {e}")
        lookup: dict[str, dict] | None = None

        def batch_jobs() -> dict[str, dict]:
            nonlocal lookup
            if lookup is None:
                lookup = self.store.batch_jobs((local.get("created_at") or now) - 600) \
                    if self.store.batch is not None else {}
            return lookup

        self._stop_jobs(folder, batch_jobs)

    def _stop_jobs(self, folder: Path, batch_jobs: Callable[[], dict[str, dict]]) -> None:
        local = read_status(folder)
        stopped = set(local.get("terminated_jobs") or [])
        ids = set()
        job = batch_jobs().get(self.store.uri(local["s3_key"])) if local.get("s3_key") else None
        if job and job["status"] not in TERMINAL_JOB_STATES:
            ids.add(job["job_id"])
        if local.get("job_id") and not job:
            ids.add(local["job_id"])  # known from the worker, but not listed (yet)
        for job_id in sorted(ids - stopped):
            self.store.terminate_job(job_id, "Cancelled from the dashboard")
            stopped.add(job_id)
            self.log(f"[{folder.name}] stopped GPU job {job_id}")
        if stopped != set(local.get("terminated_jobs") or []):
            write_status(folder, terminated_jobs=sorted(stopped))

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
                error = remote.get("error") or "Processing failed"
                self.record_failure(folder, local, remote, error)
                write_status(folder, status="failed", step="failed", error=error,
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
            error = f"The GPU job failed before processing started: {job['reason'] or 'unknown reason'}"
            self.record_failure(folder, read_status(folder), None, error)
            write_status(folder, status="failed", step="failed", job_id=job["job_id"], finished_at=time.time(),
                         error=error)
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
        from ..timing import Timings

        vid = folder.name
        step_timings = dict(remote.get("step_timings") or {})
        write_status(folder, status="processing", step="indexing", progress=STEP_PROGRESS["indexing"],
                     step_started_at=time.time(), step_timings=step_timings)
        server = Timings()  # what happens on this server, added to the worker's timeline
        try:
            with server.step("results_download", "Download results to the dashboard server", kind="io"):
                self.store.download_results(vid, folder)
            with server.step("render", "Rebuild the daily diary"):
                render_outputs(self.out_root, vid, self.settings)
        except Exception as e:  # noqa: BLE001
            write_status(folder, status="failed", step="failed", finished_at=time.time(),
                         error=f"Couldn't load the results from S3: {e}")
            return
        index_error = None
        try:
            with server.step("index", "Add to search (dashboard server)") as span:
                kwargs = {"timings": server} if "timings" in inspect.signature(self.index_fn).parameters else {}
                span["meta"]["chunks"] = self.index_fn(folder, self.settings, **kwargs)
        except (MemoryUnavailable, MemoryConfigError) as e:
            index_error = str(e)  # the diary is complete; search can be rebuilt with `lifestream index`
        index = next(s for s in server.spans if s["name"] == "index")
        if index_error:
            index["meta"]["error"] = index_error
        step_timings["indexing"] = round(index["duration"], 2)
        self._record_run(folder, local, remote, server.spans)
        write_status(folder, status="done", step="done", progress=100, finished_at=index["end"],
                     step_timings=step_timings, index_error=index_error, error=None)
        self.log(f"[{vid}] done ({len(step_timings)} steps)")

    def record_failure(self, folder: Path, local: dict, remote: dict | None, error: str) -> None:
        """A failed cloud run still goes into the run history: upload, GPU start, and the worker's
        steps up to the failure (its partial timeline.json, if it got that far)."""
        try:
            self.store.download_file(f"out/{folder.name}/timeline.json", folder / "timeline.json")
        except Exception:  # noqa: BLE001 - failed before the pipeline started: no worker timeline
            (folder / "timeline.json").unlink(missing_ok=True)
        remote = dict(remote or {})
        if not remote.get("started_at"):  # never started: GPU start lasted until now
            remote["started_at"] = time.time()
        self._record_run(folder, local, remote, [], status="failed", error=error)

    def _record_run(self, folder: Path, local: dict, remote: dict, server_spans: list[dict],
                    status: str = "done", error: str | None = None) -> None:
        """The worker's timeline plus what happened around it, saved and added to the run history:

            upload (browser -> S3) -> GPU start (wait for a machine, boot, image pull, container start)
            -> [worker: S3 download, processing steps] -> S3 upload of results
            -> download to this server, diary, indexing
        """
        from ..pipeline import runs_log_path

        path = folder / "timeline.json"
        try:
            timeline = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            timeline = {"video_id": folder.name, "spans": []}
        ours = {"upload", "s3_upload_results"} | {s["name"] for s in server_spans}
        spans = [s for s in timeline.get("spans", [])
                 if s.get("name") not in ours and s.get("name", "").split(".")[0] != "gpu_start"]
        started, uploaded, worker_start = (local.get("upload_started_at"), local.get("uploaded_at"),
                                           remote.get("started_at"))
        if started and uploaded and uploaded >= started:
            spans.append(_span("upload", "Upload (browser to S3)", started, uploaded,
                               {"bytes": local.get("size_bytes")}))
        if uploaded and worker_start and worker_start >= uploaded:
            spans += gpu_start_spans(uploaded, worker_start, remote)
        pipeline_end, worker_end = timeline.get("finished_at"), remote.get("finished_at")
        if status == "done" and pipeline_end and worker_end and worker_end >= pipeline_end:
            spans.append(_span("s3_upload_results", "Upload results to S3", pipeline_end, worker_end))
        if status == "done" and worker_end and server_spans and server_spans[0]["start"] > worker_end:
            spans.append({**_span("pickup", "Waiting for the server to notice (it checks every 5 s)",
                                  worker_end, server_spans[0]["start"]), "kind": "step"})
        spans += server_spans
        if not spans:
            return
        spans.sort(key=lambda s: s["start"])
        start = min(s["start"] for s in spans)
        end = max([s["end"] for s in spans] + ([worker_end] if status == "failed" and worker_end else []))
        timeline.update(spans=spans, status=status, error=error, started_at=start, finished_at=end,
                        total=round(end - start, 3), source="cloud", video_id=folder.name)
        timeline.setdefault("file", local.get("original_name"))
        path.write_text(json.dumps(timeline, indent=2))
        append_log(runs_log_path(self.out_root), timeline)


def gpu_start_spans(uploaded: float, worker_start: float, remote: dict) -> list[dict]:
    """Upload finished -> worker running, split by the times the worker reported (startup_times).

    Cold (a new machine booted after the upload): wait for a machine -> boot and join Batch
    -> image pull -> container start. Warm (the machine was already up): wait in the queue
    -> image pull (usually instant, cached) -> container start."""
    booted = remote.get("host_booted_at")
    cold = booted is not None and booted > uploaded
    marks = [("gpu_start.wait", "Waiting for a GPU machine (Batch + EC2 launch)" if cold
              else "Waiting in the Batch queue (machine already running)", uploaded)]
    if cold:
        marks.append(("gpu_start.boot", "Machine boot + join the Batch cluster", booted))
    marks += [("gpu_start.pull", "Pull the worker image", remote.get("pull_started_at")),
              ("gpu_start.container", "Start the container", remote.get("pull_stopped_at"))]
    # keep the reported times, in order, inside [uploaded, worker_start]
    points, last = [], uploaded
    for name, label, at in marks:
        if at is None:
            continue
        at = min(max(at, last), worker_start)
        points.append((name, label, at))
        last = at
    children = []
    for i, (name, label, at) in enumerate(points):
        end = points[i + 1][2] if i + 1 < len(points) else worker_start
        if end > at:
            children.append({**_span(name, label, at, end), "parent": "gpu_start"})
    parent = _span("gpu_start", "GPU start" + (" (cold: new machine)" if cold else ""), uploaded, worker_start,
                   {"cold": cold if booted is not None else None, "job_id": remote.get("job_id")})
    return [parent] + (children if len(children) > 1 else [])


def live_spans(status: dict, now: float) -> list[dict]:
    """Spans before the worker's timeline exists (the video's Timing tab while it waits):
    the browser upload, then the GPU start, each still running if it hasn't finished."""
    spans = []
    started, uploaded = status.get("upload_started_at"), status.get("uploaded_at")
    if started:
        spans.append({**_span("upload", "Upload (browser to S3)", started, uploaded or now,
                              {"bytes": status.get("size_bytes")}), "running": not uploaded})
    if uploaded:
        worker = status.get("started_at")
        spans.append({**_span("gpu_start", "GPU start", uploaded, worker or now,
                              {"job_id": status.get("job_id")}), "running": not worker})
        # finished worker steps, one after another (the worker reports seconds per step)
        cursor = worker
        for step in STEP_PROGRESS:
            seconds = (status.get("step_timings") or {}).get(step)
            if cursor and seconds is not None and step != "indexing":
                spans.append({**_span(f"step.{step}", STEP_LABELS.get(step, step), cursor, cursor + seconds),
                              "kind": "step", "category": STEP_CATEGORIES.get(step, "other")})
                cursor += seconds
    return spans


def _span(name: str, label: str, start: float, end: float, meta: dict | None = None) -> dict:
    return {"name": name, "label": label, "kind": "io", "parent": None, "start": start, "end": end,
            "duration": round(end - start, 3), "meta": meta or {}}
