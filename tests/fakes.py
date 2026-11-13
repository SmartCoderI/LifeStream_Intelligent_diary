"""Fake LLM and fake heavy steps, so pipeline tests run in seconds without API keys."""

from lifestream.schemas import (
    Activity, Classification, Keyframe, Location, MeetingActionItem, MeetingPoint,
    MeetingSummary, MeetingTopic, Scene, SpeakerSegment, VlogActionItem, VlogSummary, Word,
)


class FakeLLM:
    def __init__(self, kind="meeting", points=6):
        self.kind = kind
        self.points = points
        self.calls = []

    def parse(self, *, system, content, schema, max_tokens, effort):
        self.calls.append({"schema": schema.__name__, "content": content, "effort": effort})
        if schema is Classification:
            return Classification(type=self.kind, confidence="high", reason="test")
        if schema is MeetingSummary:
            return MeetingSummary(
                title="Frontend sync",
                overview="The team reviewed the redesign.",
                decisions=["Ship the navigation bar on Friday"],
                location=Location(value="Unknown", source="unknown"),
                attendees=["SPEAKER_00", "SPEAKER_01"],
                topics=[MeetingTopic(title="Navigation bar", points=[
                    MeetingPoint(speaker="SPEAKER_00", timestamp=f"00:00:{i:02d}", content=f"point {i}")
                    for i in range(self.points)
                ])],
                action_items=[MeetingActionItem(owner="SPEAKER_01", task="Fix the logo width",
                                                timestamp="00:00:05", due="Thursday")],
            )
        return VlogSummary(
            title="Morning walk",
            overview="A walk through the park.",
            location=Location(value="City park", source="inferred"),
            activities=[Activity(time="00:00:00", description="Walks through the park")],
            action_items=[],
        )


def fake_transcribe(wav, settings, **kwargs):
    return [Word(start=0.0, end=0.5, text="hello"), Word(start=0.6, end=1.0, text="there"),
            Word(start=1.5, end=2.0, text="hi"), Word(start=2.1, end=2.6, text="back")]


def fake_diarize(wav, settings, **hints):
    return [SpeakerSegment(start=0.0, end=1.2, speaker="SPEAKER_00"),
            SpeakerSegment(start=1.3, end=3.0, speaker="SPEAKER_01")]


def fake_visual(video, out_dir, duration, threshold, min_scene_len_s, max_keyframes, keyframe_width):
    return [Scene(start=0.0, end=duration)], [Keyframe(time=1.0, path="keyframes/none.jpg")]


def fake_index(out_dir, settings):
    return 0


class FakeBatch:
    """The few AWS Batch calls the dashboard server makes (moto's Batch needs Docker)."""

    def __init__(self):
        self.submitted: list[dict] = []
        self.jobs: list[dict] = []  # DescribeJobs-shaped

    def submit_job(self, **kwargs):
        self.submitted.append(kwargs)
        job_id = f"job-{len(self.submitted)}"
        self.jobs.append({"jobId": job_id, "jobName": kwargs["jobName"], "status": "SUBMITTED",
                          "parameters": kwargs["parameters"], "createdAt": 1})
        return {"jobId": job_id}

    def list_jobs(self, **kwargs):
        return {"jobSummaryList": [{"jobId": j["jobId"], "status": j["status"]} for j in self.jobs]}

    def describe_jobs(self, jobs):
        return {"jobs": [j for j in self.jobs if j["jobId"] in jobs]}


def upload_folder(s3, bucket, folder, prefix):
    """Put a local results folder into S3 the way the worker does."""
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            s3.upload_file(str(path), bucket, prefix + path.relative_to(folder).as_posix())
