"""Success-metric checks for Stage 1.

M1: % of reference speech segments attributed to the correct speaker (target > 90%).
M3b: meeting/vlog classification is correct, and meeting summaries are detailed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from scipy.optimize import linear_sum_assignment

from ..schemas import ScriptSegment, SpeakerSegment, Turn, VideoSummary
from ..intelligence.summarize import meeting_problems

Interval = SpeakerSegment | Turn | ScriptSegment


# ---------- RTTM (standard diarization label format) ----------
def load_rttm(path: str | Path) -> list[SpeakerSegment]:
    """Lines: SPEAKER <file> <chan> <start> <duration> <NA> <NA> <speaker> <NA> <NA>"""
    segments = []
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start, dur = float(parts[3]), float(parts[4])
            segments.append(SpeakerSegment(start=start, end=start + dur, speaker=parts[7]))
    return sorted(segments, key=lambda s: s.start)


def write_rttm(path: str | Path, segments: list[SpeakerSegment], file_id: str = "audio") -> None:
    lines = [
        f"SPEAKER {file_id} 1 {s.start:.3f} {s.end - s.start:.3f} <NA> <NA> {s.speaker} <NA> <NA>"
        for s in segments
    ]
    Path(path).write_text("\n".join(lines) + "\n")


def crop(segments: list[Interval], end: float) -> list[SpeakerSegment]:
    out = []
    for s in segments:
        if s.start < end:
            out.append(SpeakerSegment(start=s.start, end=min(s.end, end), speaker=s.speaker))
    return out


# ---------- M1 ----------
def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def best_mapping(reference: list[Interval], hypothesis: list[Interval]) -> dict[str, str]:
    """Hypothesis label -> reference label, chosen to maximise overlapping time.
    (Diarization labels are arbitrary, so SPEAKER_00 must first be matched to a person.)"""
    ref_labels = sorted({s.speaker for s in reference})
    hyp_labels = sorted({s.speaker for s in hypothesis})
    if not ref_labels or not hyp_labels:
        return {}
    matrix = np.zeros((len(hyp_labels), len(ref_labels)))
    for h in hypothesis:
        for r in reference:
            matrix[hyp_labels.index(h.speaker), ref_labels.index(r.speaker)] += _overlap(h.start, h.end, r.start, r.end)
    rows, cols = linear_sum_assignment(-matrix)
    return {hyp_labels[i]: ref_labels[j] for i, j in zip(rows, cols) if matrix[i, j] > 0}


@dataclass
class SpeakerAccuracy:
    segments: int
    correct: int
    mapping: dict[str, str]
    wrong: list[dict] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.segments if self.segments else 0.0


def segment_accuracy(reference: list[Interval], hypothesis: list[Interval]) -> SpeakerAccuracy:
    """A reference segment is correct if more than half of its duration is covered by
    hypothesis speech carrying the (mapped) correct label."""
    mapping = best_mapping(reference, hypothesis)
    correct = 0
    wrong = []
    for r in reference:
        length = r.end - r.start
        if length <= 0:
            continue
        right_time = sum(
            _overlap(h.start, h.end, r.start, r.end)
            for h in hypothesis if mapping.get(h.speaker) == r.speaker
        )
        if right_time > 0.5 * length:
            correct += 1
        else:
            wrong.append({"start": round(r.start, 2), "end": round(r.end, 2), "speaker": r.speaker,
                          "correct_share": round(right_time / length, 2)})
    total = sum(1 for r in reference if r.end > r.start)
    return SpeakerAccuracy(segments=total, correct=correct, mapping=mapping, wrong=wrong)


def der(reference: list[Interval], hypothesis: list[Interval], collar: float = 0.25) -> float:
    """Diarization error rate (pyannote.metrics), standard 0.25 s collar."""
    from pyannote.core import Annotation, Segment
    from pyannote.metrics.diarization import DiarizationErrorRate

    def to_annotation(items: list[Interval]) -> Annotation:
        ann = Annotation()
        for i, s in enumerate(items):
            if s.end > s.start:
                ann[Segment(s.start, s.end), i] = s.speaker
        return ann

    metric = DiarizationErrorRate(collar=collar, skip_overlap=False)
    return float(metric(to_annotation(reference), to_annotation(hypothesis)))


# ---------- M3b ----------
@dataclass
class ClassifyResult:
    video_id: str
    expected: str
    actual: str | None
    problems: list[str]

    @property
    def passed(self) -> bool:
        return self.actual == self.expected and not self.problems


def check_classification(labels_dir: str | Path, out_root: str | Path,
                         min_points_per_10min: float = 3.0) -> list[ClassifyResult]:
    """For each labels/*.yaml with `video_id` and `type`, compare against summary.json."""
    results = []
    for label_file in sorted(Path(labels_dir).glob("*.yaml")):
        label = yaml.safe_load(label_file.read_text()) or {}
        if "video_id" not in label or "type" not in label:
            continue
        summary_path = Path(out_root) / label["video_id"] / "summary.json"
        if not summary_path.exists():
            results.append(ClassifyResult(label["video_id"], label["type"], None, ["not processed yet"]))
            continue
        v = VideoSummary.model_validate(json.loads(summary_path.read_text()))
        problems = []
        if v.classification.type == "meeting":
            if v.meeting is None:
                problems.append("classified as meeting but has no meeting summary")
            else:
                problems += meeting_problems(v.meeting, v.duration, min_points_per_10min)
        results.append(ClassifyResult(v.video_id, label["type"], v.classification.type, problems))
    return results
