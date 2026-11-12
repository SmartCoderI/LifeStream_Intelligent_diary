"""F10: speaker registry. Maps each video's speaker labels to real names.

Stored as YAML, keyed by video, because diarization labels restart in every
video (SPEAKER_00 in one video is not SPEAKER_00 in another):

    videos:
      multiple-student:
        SPEAKER_00: Ms. Lee
        SPEAKER_04: Principal Smith

Labels without a name are left as they are. The pipeline keeps raw labels in
its saved files and applies names when it renders, serves or indexes them, so
renaming someone never requires re-processing a video.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

LABEL = re.compile(r"\b(SPEAKER_\d+)\b(?: \(([^)]*)\))?")
T = TypeVar("T")


class RegistryError(Exception):
    pass


class SpeakerRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.videos: dict[str, dict[str, str]] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = yaml.safe_load(self.path.read_text()) or {}
        except yaml.YAMLError as e:
            raise RegistryError(f"{self.path} is not valid YAML: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("videos", {}), dict):
            raise RegistryError(f"{self.path} must contain a 'videos:' mapping")
        for video_id, names in (data.get("videos") or {}).items():
            if not isinstance(names, dict):
                raise RegistryError(f"{self.path}: entry for {video_id!r} must map labels to names")
            self.videos[str(video_id)] = {
                str(k): str(v).strip() for k, v in names.items() if v is not None and str(v).strip()
            }

    def names_for(self, video_id: str) -> dict[str, str]:
        return dict(self.videos.get(video_id, {}))

    def set_names(self, video_id: str, names: dict[str, str | None]) -> dict[str, str]:
        """Update names for one video. An empty name removes that label's name."""
        current = self.videos.setdefault(video_id, {})
        for label, name in names.items():
            if not re.fullmatch(r"SPEAKER_\d+|UNKNOWN", label):
                raise RegistryError(f"Not a speaker label: {label!r}")
            clean = (name or "").strip()
            if clean:
                current[label] = clean
            else:
                current.pop(label, None)
        if not current:
            self.videos.pop(video_id, None)
        self.save()
        return self.names_for(video_id)

    def remove_video(self, video_id: str) -> None:
        if self.videos.pop(video_id, None) is not None:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump({"videos": self.videos}, sort_keys=True, allow_unicode=True))
        tmp.replace(self.path)


def rename_text(text: str, names: dict[str, str]) -> str:
    """Replace labels in free text. 'SPEAKER_00 (Ali)' becomes just the registered name."""
    if not names:
        return text

    def sub(m: re.Match) -> str:
        return names.get(m.group(1), m.group(0))

    return LABEL.sub(sub, text)


def apply_names(value: T, names: dict[str, str]) -> T:
    """Return a copy of a model / dict / list / string with speaker labels replaced."""
    if not names:
        return value
    if isinstance(value, BaseModel):
        return type(value).model_validate(apply_names(value.model_dump(), names))
    if isinstance(value, str):
        return rename_text(value, names)  # type: ignore[return-value]
    if isinstance(value, dict):
        return {k: apply_names(v, names) for k, v in value.items()}  # type: ignore[return-value]
    if isinstance(value, list):
        return [apply_names(v, names) for v in value]  # type: ignore[return-value]
    return value


def label_of(value: Any) -> str | None:
    m = LABEL.search(str(value))
    return m.group(1) if m else None
