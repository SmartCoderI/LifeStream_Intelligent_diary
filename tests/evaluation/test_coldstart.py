"""M3a: lining up Batch / ECS / EC2 timestamps into a cold-start report."""

import datetime as dt

from lifestream.evaluation.coldstart import breakdown

T0 = dt.datetime(2026, 11, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def ms(seconds: float) -> int:
    return int(at(seconds).timestamp() * 1000)


JOB = {"jobId": "j-1", "status": "SUCCEEDED", "createdAt": ms(0), "startedAt": ms(400), "stoppedAt": ms(700)}
TASK = {"pullStartedAt": at(130), "pullStoppedAt": at(380)}
TIMELINE = {"video_duration": 98.5, "spans": [
    {"name": "transcribe.load_model", "kind": "load", "duration": 12.0},
    {"name": "diarize.load_model", "kind": "load", "duration": 3.5},
    {"name": "transcribe.asr", "kind": "step", "duration": 40.0},
]}
WORKER = {"gpu": "Tesla T4", "download_s": 2.0, "pipeline_s": 250.0, "upload_s": 4.0}


def test_cold_start_stages():
    report = breakdown(JOB, TASK, {"registeredAt": at(110)},
                       {"LaunchTime": at(20), "InstanceType": "g4dn.xlarge"}, TIMELINE, WORKER)
    assert report["cold"] is True
    assert report["instance_type"] == "g4dn.xlarge" and report["gpu"] == "Tesla T4"
    assert report["stages_s"] == {
        "wait_for_instance": 20.0, "instance_boot": 90.0, "scheduling": 20.0, "image_pull": 250.0,
        "container_start": 20.0, "download_video": 2.0, "model_load": 15.5, "processing": 250.0,
        "upload_results": 4.0,
    }
    assert report["cold_start_s"] == 400.0
    assert report["cold_start_with_models_s"] == 415.5
    assert report["end_to_end_s"] == 700.0
    assert report["video_duration_s"] == 98.5


def test_warm_instance_is_not_cold():
    report = breakdown(JOB, TASK, {"registeredAt": at(-600)}, {"LaunchTime": at(-700)})
    assert report["cold"] is False
    assert report["stages_s"]["wait_for_instance"] is None
    assert report["stages_s"]["scheduling"] == 130.0


def test_failed_job_without_details():
    report = breakdown({"jobId": "j-2", "status": "FAILED", "statusReason": "Essential container exited",
                        "createdAt": ms(0), "stoppedAt": ms(50)})
    assert report["status_reason"] == "Essential container exited"
    assert report["cold_start_s"] is None and report["end_to_end_s"] == 50.0
