"""F7 worker: one AWS Batch job processes one video.

    s3://<bucket>/uploads/<video_id>/<file>  ->  Stage 1 pipeline  ->  s3://<bucket>/out/<video_id>/...

The job role may only read `uploads/*` and write `out/*` (see infra/iam.tf).
Besides the pipeline outputs, `worker.json` records how long each part of the job took;
the M3a cold-start check reads it together with the Batch / ECS / EC2 timestamps.

F11 (Stage 3): uploads from the dashboard start the job automatically (EventBridge, see
infra/events.tf). The recording date comes from the upload's S3 metadata (`x-amz-meta-date`),
and `out/<video_id>/status.json` is rewritten at every step so the dashboard can show progress.
Search indexing isn't done here: the dashboard server does it when it sees `done`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..config import Settings

VIDEO_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STATUS_FILE = "status.json"

def parse_s3_uri(uri: str) -> tuple[str, str]:
    """'s3://bucket/a/b' -> ('bucket', 'a/b')."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not an s3:// URI: {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def resolve_date(date: str | None, uploaded_date: str | None = None) -> str:
    """Batch job parameters can't be empty, so 'today' (or nothing) means: the date stored with
    the upload, or else the job's run date."""
    if not date or date == "today":
        date = uploaded_date or dt.date.today().isoformat()
    dt.date.fromisoformat(date)  # validate
    return date


def video_id_from_key(key: str) -> str | None:
    """'uploads/<video_id>/<file>' (dashboard uploads) -> video_id; other layouts -> None."""
    parts = key.split("/")
    if len(parts) == 3 and parts[0] == "uploads" and VIDEO_ID.match(parts[1]):
        return parts[1]
    return None


def uploaded_date(s3, bucket: str, key: str) -> str | None:
    """The recording date the dashboard stored as object metadata, if any."""
    try:
        return s3.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("date")
    except Exception:  # noqa: BLE001 - optional; fall back to the job parameter / today
        return None


class StatusReporter:
    """Writes out/<video_id>/status.json: status, current step, and seconds per finished step.
    A failed write is logged, never fatal: progress display mustn't break a job."""

    def __init__(self, s3, bucket: str, key: str, **fields):
        from ..api.jobs import STEP_PROGRESS

        self._progress = STEP_PROGRESS
        self.s3, self.bucket, self.key = s3, bucket, key
        now = time.time()
        self.data = {"status": "processing", "step": "validating", "progress": 2, "error": None,
                     "started_at": now, "step_started_at": now, "finished_at": None,
                     "step_timings": {}, **fields}
        self._save()

    def _close_step(self, now: float) -> None:
        step, started = self.data["step"], self.data["step_started_at"]
        if step and started:
            timings = self.data["step_timings"]
            timings[step] = round(timings.get(step, 0) + now - started, 2)

    def step(self, name: str) -> None:
        if name == "indexing":  # done by the dashboard server, which can reach the database
            return
        now = time.time()
        self._close_step(now)
        self.data.update(step=name, progress=self._progress.get(name, self.data["progress"]),
                         step_started_at=now)
        self._save()

    def finish(self, status: str, error: str | None = None) -> None:
        now = time.time()
        self._close_step(now)
        self.data.update(status=status, step=status, error=error, finished_at=now, step_started_at=None,
                         progress=self._progress["indexing"] if status == "done" else self.data["progress"])
        self._save()

    def _save(self) -> None:
        self.data["updated_at"] = time.time()
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self.key, Body=json.dumps(self.data).encode(),
                               ContentType="application/json")
        except Exception as e:  # noqa: BLE001
            print(f"Couldn't save status to s3://{self.bucket}/{self.key}: {e}", flush=True)


def gpu_name() -> str | None:
    try:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001 - informational only
        return None


def run_job(
    input_uri: str,
    output_uri: str,
    date: str | None,
    settings: Settings,
    *,
    s3,
    work_dir: Path | None = None,
    run_llm: bool = True,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    process: Callable[..., Path] | None = None,
) -> dict:
    """Download, process, upload. Returns the report that is also saved as worker.json.
    Errors propagate so the container exits non-zero and Batch marks the job FAILED."""
    from ..pipeline import video_id_for

    if process is None:
        from functools import partial

        from ..pipeline import Components, process_video

        # the GPU job can't reach the search database; the dashboard server indexes the results
        process = partial(process_video, components=Components(index=lambda out, s: None))

    in_bucket, in_key = parse_s3_uri(input_uri)
    out_bucket, out_prefix = parse_s3_uri(output_uri)
    date = resolve_date(date, uploaded_date(s3, in_bucket, in_key))
    vid = video_id_from_key(in_key) or video_id_for(in_key)
    prefix = f"{out_prefix.rstrip('/')}/{vid}/" if out_prefix.strip("/") else f"{vid}/"
    status = StatusReporter(s3, out_bucket, prefix + STATUS_FILE, video_id=vid, date=date, input=input_uri,
                            job_id=os.environ.get("AWS_BATCH_JOB_ID"),
                            job_attempt=os.environ.get("AWS_BATCH_JOB_ATTEMPT"))
    work = Path(work_dir or tempfile.mkdtemp(prefix="lifestream-"))
    local_video = work / "in" / Path(in_key).name
    local_video.parent.mkdir(parents=True, exist_ok=True)

    try:
        t0 = time.time()
        s3.download_file(in_bucket, in_key, str(local_video))
        t1 = time.time()
        download_span = {"name": "s3_download", "label": "Download video from S3", "kind": "io",
                         "parent": None, "start": t0, "end": t1, "duration": round(t1 - t0, 3),
                         "meta": {"bytes": local_video.stat().st_size}}

        folder = process(local_video, date, work / "out", settings, run_llm=run_llm,
                         num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers,
                         extra_spans=[download_span], source="batch", video_id=vid, on_step=status.step)
        t2 = time.time()

        status.step("uploading_results")
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        for path in files:
            s3.upload_file(str(path), out_bucket, prefix + path.relative_to(folder).as_posix())
        t3 = time.time()
    except Exception as e:
        status.finish("failed", str(e) or type(e).__name__)
        raise

    report = {
        "job_id": os.environ.get("AWS_BATCH_JOB_ID"),
        "job_attempt": os.environ.get("AWS_BATCH_JOB_ATTEMPT"),
        "input": input_uri,
        "output": f"s3://{out_bucket}/{prefix}",
        "video_id": folder.name,
        "date": date,
        "run_llm": run_llm,
        "gpu": gpu_name(),
        "started_at": t0,
        "finished_at": t3,
        "download_s": round(t1 - t0, 3),
        "pipeline_s": round(t2 - t1, 3),
        "upload_s": round(t3 - t2, 3),
        "files_uploaded": len(files),
        "settings": {"device": settings.device, "whisper_model": settings.whisper_model,
                     "compute_type": settings.compute_type,
                     "diarization_device": settings.diarization_device},
    }
    s3.put_object(Bucket=out_bucket, Key=prefix + "worker.json",
                  Body=json.dumps(report, indent=2).encode(), ContentType="application/json")
    status.finish("done")  # last: the dashboard downloads the results once it sees this
    return report
