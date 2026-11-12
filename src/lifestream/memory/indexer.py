"""Connects processed video folders (data/out/<id>/) to the search index."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from ..config import Settings
from ..schemas import ScriptSegment, VideoSummary
from .embeddings import get_embedder
from .registry import SpeakerRegistry
from .store import MemoryStore


def open_store(settings: Settings, schema: str = "public") -> MemoryStore:
    """Connect to the search database. Checks the connection before loading the
    embedding model (slow), so a stopped database fails fast."""
    import psycopg

    from .store import MemoryUnavailable

    try:
        psycopg.connect(settings.database_url, connect_timeout=3).close()
    except psycopg.OperationalError as e:
        raise MemoryUnavailable(
            "Can't reach the search database. Start it with `docker compose up -d db`.") from e
    return MemoryStore(settings.database_url, get_embedder(settings.embed_model), schema)


def load_output(out_dir: Path) -> tuple[VideoSummary, list[ScriptSegment]] | None:
    """Summary + script of a finished video, or None if it isn't finished."""
    summary_path, script_path = out_dir / "summary.json", out_dir / "script.json"
    if not summary_path.exists() or not script_path.exists():
        return None
    summary = VideoSummary.model_validate(json.loads(summary_path.read_text()))
    script = TypeAdapter(list[ScriptSegment]).validate_json(script_path.read_text())
    return summary, script


def index_output(out_dir: Path, settings: Settings, store: MemoryStore | None = None) -> int | None:
    """Index one finished video (with current speaker names). Returns the chunk count."""
    loaded = load_output(Path(out_dir))
    if loaded is None:
        return None
    summary, script = loaded
    names = SpeakerRegistry(settings.speakers_file).names_for(summary.video_id)
    own = store is None
    store = store or open_store(settings)
    try:
        return store.index_video(summary, script, names)
    finally:
        if own:
            store.close()
