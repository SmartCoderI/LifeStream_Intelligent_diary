"""Data shapes shared by every pipeline step. All times are in seconds."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

UNKNOWN_SPEAKER = "UNKNOWN"


# ---------- F1: media ----------
class MediaInfo(BaseModel):
    path: str
    container: str
    duration: float
    video_codec: str
    audio_codec: str
    width: int
    height: int
    fps: float
    audio_sample_rate: int
    audio_channels: int


# ---------- F2 / F3: speech ----------
class Word(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None


class SpeakerSegment(BaseModel):
    start: float
    end: float
    speaker: str


class Turn(BaseModel):
    start: float
    end: float
    speaker: str
    text: str


# ---------- F4 / F5: visuals ----------
class Scene(BaseModel):
    start: float
    end: float


class Keyframe(BaseModel):
    time: float
    path: str


class ScriptSegment(BaseModel):
    """One speaker turn plus the keyframes that fall in it (F5 output)."""

    start: float
    end: float
    speaker: str
    text: str
    frames: list[str] = Field(default_factory=list)


# ---------- F6: LLM outputs ----------
# These models are sent to the LLM as the required JSON schema, so keep them
# plain (no custom validators) and describe each field.
class Classification(BaseModel):
    type: Literal["meeting", "vlog"]
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(description="One sentence explaining the choice.")


class Location(BaseModel):
    value: str = Field(description="Place name, or 'Unknown'.")
    source: Literal["metadata", "inferred", "unknown"]


class Activity(BaseModel):
    time: str = Field(description="Start time in the video, HH:MM:SS.")
    description: str


class VlogActionItem(BaseModel):
    owner: str | None = None
    task: str


class VlogSummary(BaseModel):
    title: str
    location: Location
    activities: list[Activity]
    action_items: list[VlogActionItem] = Field(description="Empty list if there are none.")


class MeetingPoint(BaseModel):
    speaker: str = Field(description="Speaker label exactly as it appears in the transcript.")
    timestamp: str = Field(description="When it was said, HH:MM:SS.")
    content: str = Field(description="What was said, in one or two sentences.")


class MeetingTopic(BaseModel):
    title: str
    points: list[MeetingPoint]


class MeetingActionItem(BaseModel):
    owner: str = Field(description="Speaker label of the person responsible, or 'Unassigned'.")
    task: str
    timestamp: str = Field(description="When it was agreed, HH:MM:SS.")
    due: str | None = None


class MeetingSummary(BaseModel):
    title: str
    location: Location
    attendees: list[str]
    topics: list[MeetingTopic]
    action_items: list[MeetingActionItem] = Field(description="Empty list if there are none.")


class VideoSummary(BaseModel):
    """What the pipeline stores per video (summary.json)."""

    video_id: str
    date: str
    duration: float
    classification: Classification
    vlog: VlogSummary | None = None
    meeting: MeetingSummary | None = None
