"""Records how long each pipeline step takes (written to timings.json)."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class Timings:
    def __init__(self) -> None:
        self.steps: dict[str, float] = {}
        self._lock = threading.Lock()

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, time.perf_counter() - start)

    def record(self, name: str, seconds: float) -> None:
        with self._lock:
            self.steps[name] = round(seconds, 2)

    def save(self, path: Path, total: float | None = None) -> None:
        data = dict(self.steps)
        if total is not None:
            data["total"] = round(total, 2)
        path.write_text(json.dumps(data, indent=2))


def fmt_ts(seconds: float) -> str:
    """Seconds -> HH:MM:SS."""
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_ts(text: str) -> float | None:
    """'HH:MM:SS', 'MM:SS' or 'SS' -> seconds. Returns None if it can't be parsed."""
    try:
        parts = [float(p) for p in text.strip().split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total
