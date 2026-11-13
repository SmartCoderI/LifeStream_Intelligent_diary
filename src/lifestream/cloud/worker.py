"""F7 worker: one AWS Batch job processes one video.

    s3://<bucket>/uploads/<file>  ->  Stage 1 pipeline  ->  s3://<bucket>/out/<video_id>/...

The job role may only read `uploads/*` and write `out/*` (see infra/iam.tf).
Besides the pipeline outputs, `worker.json` records how long each part of the job took;
the M3a cold-start check reads it together with the Batch / ECS / EC2 timestamps.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..config import Settings


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """'s3://bucket/a/b' -> ('bucket', 'a/b')."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Not an s3:// URI: {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def resolve_date(date: str | None) -> str:
    """Batch job parameters can't be empty, so 'today' (or nothing) means the job's run date."""
    if not date or date == "today":
        return dt.date.today().isoformat()
    dt.date.fromisoformat(date)  # validate
    return date


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
    if process is None:
        from ..pipeline import process_video as process

    date = resolve_date(date)
    in_bucket, in_key = parse_s3_uri(input_uri)
    out_bucket, out_prefix = parse_s3_uri(output_uri)
    work = Path(work_dir or tempfile.mkdtemp(prefix="lifestream-"))
    local_video = work / "in" / Path(in_key).name
    local_video.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    s3.download_file(in_bucket, in_key, str(local_video))
    t1 = time.time()
    download_span = {"name": "s3_download", "label": "Download video from S3", "kind": "io",
                     "parent": None, "start": t0, "end": t1, "duration": round(t1 - t0, 3),
                     "meta": {"bytes": local_video.stat().st_size}}

    folder = process(local_video, date, work / "out", settings, run_llm=run_llm,
                     num_speakers=num_speakers, min_speakers=min_speakers, max_speakers=max_speakers,
                     extra_spans=[download_span], source="batch")
    t2 = time.time()

    prefix = f"{out_prefix.rstrip('/')}/{folder.name}/" if out_prefix.strip("/") else f"{folder.name}/"
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    for path in files:
        s3.upload_file(str(path), out_bucket, prefix + path.relative_to(folder).as_posix())
    t3 = time.time()

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
    return report
