"""M3a: cold start of a GPU job on AWS Batch.

Submits one job, waits for it, then lines up the timestamps AWS records for each stage:

    submitted (Batch) -> instance launched (EC2) -> instance ready (ECS registration)
      -> image pull start/end (ECS) -> container running (Batch) -> models loaded (timeline.json)
      -> job finished (Batch)

"Cold" means Batch had to launch a new instance for this job (the compute environment
scales to zero, so the first job after idle is always cold).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from pathlib import Path
from typing import Callable

TERMINAL = {"SUCCEEDED", "FAILED"}


def _ts(value) -> float | None:
    """Batch gives epoch milliseconds; ECS/EC2 give datetimes. Return epoch seconds."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.timestamp()
    return float(value) / 1000.0


def _gap(a: float | None, b: float | None) -> float | None:
    return round(b - a, 1) if a is not None and b is not None else None


def breakdown(job: dict, task: dict | None = None, container_instance: dict | None = None,
              instance: dict | None = None, timeline: dict | None = None,
              worker: dict | None = None) -> dict:
    """Combine Batch job, ECS task / container instance, EC2 instance, the run's timeline.json
    and worker.json into one cold-start report (seconds)."""
    submitted = _ts(job.get("createdAt"))
    running = _ts(job.get("startedAt"))
    stopped = _ts(job.get("stoppedAt"))
    launched = _ts(instance.get("LaunchTime")) if instance else None
    registered = _ts(container_instance.get("registeredAt")) if container_instance else None
    pull_start = _ts(task.get("pullStartedAt")) if task else None
    pull_end = _ts(task.get("pullStoppedAt")) if task else None
    cold = launched is not None and submitted is not None and launched >= submitted

    spans = (timeline or {}).get("spans", [])
    model_load = sum(s.get("duration", 0) for s in spans if s.get("kind") == "load")
    worker = worker or {}

    return {
        "job_id": job.get("jobId"),
        "status": job.get("status"),
        "status_reason": job.get("statusReason"),
        "instance_type": (instance or {}).get("InstanceType"),
        "gpu": worker.get("gpu"),
        "video_duration_s": (timeline or {}).get("video_duration"),
        "cold": cold,
        "stages_s": {
            "wait_for_instance": _gap(submitted, launched) if cold else None,
            "instance_boot": _gap(launched, registered) if cold else None,
            "scheduling": _gap(registered if cold else submitted, pull_start),
            "image_pull": _gap(pull_start, pull_end),
            "container_start": _gap(pull_end, running),
            "download_video": worker.get("download_s"),
            "model_load": round(model_load, 1) if spans else None,   # part of processing
            "processing": worker.get("pipeline_s"),
            "upload_results": worker.get("upload_s"),
        },
        "cold_start_s": _gap(submitted, running),            # submit -> container running
        "cold_start_with_models_s": (round(_gap(submitted, running) + model_load, 1)
                                     if running and submitted and spans else None),
        "end_to_end_s": _gap(submitted, stopped),
    }


def terraform_outputs(infra_dir: Path) -> dict:
    """Read names created by Terraform (bucket, queue, job definition, ...)."""
    raw = subprocess.run(["terraform", f"-chdir={infra_dir}", "output", "-json"],
                         check=True, capture_output=True, text=True).stdout
    return {k: v["value"] for k, v in json.loads(raw).items()}


def run_coldstart(video: Path, date: str, *, outputs: dict, session, poll_s: float = 10.0,
                  run_llm: bool = True, timeout_s: float = 3 * 3600,
                  log: Callable[[str], None] = print) -> dict:
    """Upload a test video, run one Batch job on it, and return the cold-start report."""
    from ..pipeline import video_id_for

    s3, batch = session.client("s3"), session.client("batch")
    ecs, ec2 = session.client("ecs"), session.client("ec2")
    bucket = outputs["bucket_name"]
    key = f"uploads/{video.name}"
    log(f"Uploading {video} to s3://{bucket}/{key} ...")
    s3.upload_file(str(video), bucket, key)

    params = {"input": f"s3://{bucket}/{key}", "output": f"s3://{bucket}/out/", "date": date}
    submit = {"jobName": f"m3a-{int(time.time())}", "jobQueue": outputs["job_queue"],
              "jobDefinition": outputs["job_definition"], "parameters": params}
    if not run_llm:
        submit["containerOverrides"] = {"command": ["worker", params["input"], "--output",
                                                    params["output"], "--date", date, "--no-llm"]}
    job_id = batch.submit_job(**submit)["jobId"]
    log(f"Submitted job {job_id}")

    t0, last = time.time(), None
    while True:
        job = batch.describe_jobs(jobs=[job_id])["jobs"][0]
        if job["status"] != last:
            last = job["status"]
            log(f"  {time.time() - t0:7.0f}s  {last}")
        if last in TERMINAL:
            break
        if time.time() - t0 > timeout_s:
            raise TimeoutError(f"Job {job_id} still {last} after {timeout_s:.0f}s")
        time.sleep(poll_s)

    task = container_instance = instance = None
    attempt = (job.get("attempts") or [{}])[-1].get("container", {})
    env = batch.describe_compute_environments(computeEnvironments=[outputs["compute_environment"]])
    cluster = env["computeEnvironments"][0].get("ecsClusterArn")
    if cluster and attempt.get("taskArn"):
        tasks = ecs.describe_tasks(cluster=cluster, tasks=[attempt["taskArn"]])["tasks"]
        task = tasks[0] if tasks else None
    if cluster and attempt.get("containerInstanceArn"):
        found = ecs.describe_container_instances(
            cluster=cluster, containerInstances=[attempt["containerInstanceArn"]])["containerInstances"]
        container_instance = found[0] if found else None
    if container_instance and container_instance.get("ec2InstanceId"):
        reservations = ec2.describe_instances(InstanceIds=[container_instance["ec2InstanceId"]])
        instance = reservations["Reservations"][0]["Instances"][0]

    timeline = worker = None
    if job["status"] == "SUCCEEDED":
        prefix = f"out/{video_id_for(video)}/"
        timeline = _read_json(s3, bucket, prefix + "timeline.json")
        worker = _read_json(s3, bucket, prefix + "worker.json")
    return breakdown(job, task, container_instance, instance, timeline, worker)


def _read_json(s3, bucket: str, key: str) -> dict | None:
    try:
        with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
            return json.loads(body.read())
    except s3.exceptions.NoSuchKey:
        return None
