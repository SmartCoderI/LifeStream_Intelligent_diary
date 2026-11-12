"""F8 / F9: Postgres + pgvector store for chunks, and vector search.

Tables (inside one Postgres schema, `public` by default; evals use their own):
    videos(id, date, type, title, duration, indexed_at)
    turns(video_id, start_ts, end_ts, speaker, text)
    chunks(id, video_id, date, kind, section, start_ts, end_ts, speakers, text, embedding)
    speaker_map(video_id, speaker_id, real_name)
    meta(key, value)            embedding model name + dimension
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import numpy as np

from ..schemas import ScriptSegment, VideoSummary
from ..timing import sub_step
from .chunking import Chunk, summary_chunks, transcript_chunks
from .embeddings import Embedder
from .registry import apply_names

SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
SUMMARY_BOOST = 0.05  # summary paragraphs rank slightly above raw transcript


class MemoryUnavailable(Exception):
    """The database can't be reached."""


class MemoryConfigError(Exception):
    """The database is reachable but not usable as configured."""


@dataclass
class SearchHit:
    chunk_id: int
    video_id: str
    title: str
    date: str
    kind: str
    section: str
    start: float
    end: float
    text: str
    score: float


def _specific_first(hits: list[SearchHit]) -> list[SearchHit]:
    """Parent -> child: a video's overview is good at finding the right video, but the
    answer lives in a specific paragraph. When a video's overview outranks all its other
    summary paragraphs, put the best specific one first, right before the overview."""
    out: list[SearchHit] = []
    used: set[int] = set()
    for h in hits:
        if h.chunk_id in used:
            continue
        if h.kind == "summary" and h.section == "Summary":
            child = next((c for c in hits if c.video_id == h.video_id and c.kind == "summary"
                          and c.section != "Summary" and c.chunk_id not in used), None)
            if child is not None:
                out.append(child)
                used.add(child.chunk_id)
        out.append(h)
        used.add(h.chunk_id)
    return out


class MemoryStore:
    def __init__(self, database_url: str, embedder: Embedder, schema: str = "public"):
        import psycopg
        from pgvector.psycopg import register_vector

        if not SCHEMA_NAME.match(schema):
            raise ValueError(f"Bad schema name {schema!r}")
        self.schema = schema
        self.embedder = embedder
        try:
            self.conn = psycopg.connect(database_url, autocommit=True, connect_timeout=5)
        except psycopg.OperationalError as e:
            raise MemoryUnavailable(
                "Can't reach the search database. Start it with `docker compose up -d db`."
            ) from e
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- schema ----------
    def _ensure_schema(self) -> None:
        s, dim = self.schema, self.embedder.dim
        self.conn.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {s}.meta (key text PRIMARY KEY, value text NOT NULL);
            CREATE TABLE IF NOT EXISTS {s}.videos (
                id text PRIMARY KEY, date date NOT NULL, type text, title text,
                duration real, indexed_at timestamptz NOT NULL DEFAULT now());
            CREATE TABLE IF NOT EXISTS {s}.turns (
                id bigserial PRIMARY KEY,
                video_id text NOT NULL REFERENCES {s}.videos(id) ON DELETE CASCADE,
                start_ts real NOT NULL, end_ts real NOT NULL, speaker text NOT NULL, text text NOT NULL);
            CREATE TABLE IF NOT EXISTS {s}.chunks (
                id bigserial PRIMARY KEY,
                video_id text NOT NULL REFERENCES {s}.videos(id) ON DELETE CASCADE,
                date date NOT NULL,
                kind text NOT NULL CHECK (kind IN ('transcript', 'summary')),
                section text NOT NULL, start_ts real NOT NULL, end_ts real NOT NULL,
                speakers text[] NOT NULL DEFAULT '{{}}', text text NOT NULL,
                embedding vector({dim}) NOT NULL);
            CREATE TABLE IF NOT EXISTS {s}.speaker_map (
                video_id text NOT NULL REFERENCES {s}.videos(id) ON DELETE CASCADE,
                speaker_id text NOT NULL, real_name text NOT NULL,
                PRIMARY KEY (video_id, speaker_id));
            CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON {s}.chunks
                USING hnsw (embedding vector_cosine_ops);
            CREATE INDEX IF NOT EXISTS chunks_date ON {s}.chunks (date);
            CREATE INDEX IF NOT EXISTS chunks_video ON {s}.chunks (video_id);
            CREATE INDEX IF NOT EXISTS turns_video ON {s}.turns (video_id);
        """)
        row = self.conn.execute(f"SELECT value FROM {s}.meta WHERE key = 'embed_model'").fetchone()
        if row is None:
            self.conn.execute(f"INSERT INTO {s}.meta VALUES ('embed_model', %s), ('embed_dim', %s)",
                              (self.embedder.name, str(dim)))
        elif row[0] != self.embedder.name:
            raise MemoryConfigError(
                f"The index was built with {row[0]!r} but EMBED_MODEL is {self.embedder.name!r}. "
                "Run `lifestream index --rebuild` to re-embed everything.")

    def reset(self) -> None:
        """Drop and recreate this schema's tables (used by --rebuild and evals)."""
        self.conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        self._ensure_schema()

    # ---------- writing ----------
    def index_video(
        self,
        summary: VideoSummary,
        script: list[ScriptSegment],
        names: dict[str, str] | None = None,
        timings=None,
    ) -> int:
        """(Re)index one video. Speaker names are applied first. Returns the number of chunks."""
        names = names or {}
        named_summary = apply_names(summary, names)
        named_script = apply_names(script, names)
        body = named_summary.meeting or named_summary.vlog
        title = body.title if body else summary.video_id

        chunks: list[Chunk] = summary_chunks(named_summary) + transcript_chunks(
            summary.video_id, summary.date, title, summary.classification.type, named_script)
        with sub_step(timings, "index.embed", "Embed chunks", parent="index") as span:
            vectors = self.embedder.embed_documents([c.to_embed() for c in chunks])
            span["meta"]["chunks"] = len(chunks)

        s = self.schema
        with sub_step(timings, "index.db", "Write to database", parent="index"), self.conn.transaction():
            self.conn.execute(f"DELETE FROM {s}.videos WHERE id = %s", (summary.video_id,))
            self.conn.execute(
                f"INSERT INTO {s}.videos (id, date, type, title, duration) VALUES (%s, %s, %s, %s, %s)",
                (summary.video_id, summary.date, summary.classification.type, title, summary.duration))
            with self.conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {s}.turns (video_id, start_ts, end_ts, speaker, text) VALUES (%s, %s, %s, %s, %s)",
                    [(summary.video_id, t.start, t.end, t.speaker, t.text) for t in named_script])
                cur.executemany(
                    f"INSERT INTO {s}.chunks (video_id, date, kind, section, start_ts, end_ts, speakers, text, embedding)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(c.video_id, c.date, c.kind, c.section, c.start, c.end, c.speakers, c.text, vec)
                     for c, vec in zip(chunks, vectors)])
                cur.executemany(
                    f"INSERT INTO {s}.speaker_map (video_id, speaker_id, real_name) VALUES (%s, %s, %s)",
                    [(summary.video_id, label, name) for label, name in names.items()])
        return len(chunks)

    def delete_video(self, video_id: str) -> None:
        self.conn.execute(f"DELETE FROM {self.schema}.videos WHERE id = %s", (video_id,))

    # ---------- reading ----------
    def count(self) -> dict[str, int]:
        s = self.schema
        videos = self.conn.execute(f"SELECT count(*) FROM {s}.videos").fetchone()[0]
        chunks = self.conn.execute(f"SELECT count(*) FROM {s}.chunks").fetchone()[0]
        return {"videos": videos, "chunks": chunks}

    def indexed_videos(self) -> set[str]:
        return {r[0] for r in self.conn.execute(f"SELECT id FROM {self.schema}.videos").fetchall()}

    def search(
        self,
        query: str,
        k: int = 6,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        timings=None,
    ) -> list[SearchHit]:
        """Top-k chunks by cosine similarity (summary paragraphs get a small boost),
        optionally limited to a date range (inclusive)."""
        with sub_step(timings, "search.embed", "Embed question", parent="search"):
            vec = self.embedder.embed_query(query)
        s = self.schema
        where, params = [], []
        if date_from:
            where.append("c.date >= %s")
            params.append(date_from)
        if date_to:
            where.append("c.date <= %s")
            params.append(date_to)
        sql = f"""
            SELECT c.id, c.video_id, v.title, c.date, c.kind, c.section, c.start_ts, c.end_ts, c.text,
                   1 - (c.embedding <=> %s) AS similarity
            FROM {s}.chunks c JOIN {s}.videos v ON v.id = c.video_id
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY c.embedding <=> %s
            LIMIT %s"""
        with sub_step(timings, "search.db", "Vector query (pgvector)", parent="search"), self.conn.transaction():
            # search more of the HNSW graph so date filters still leave enough matches
            self.conn.execute("SET LOCAL hnsw.ef_search = 200")
            rows = self.conn.execute(sql, [np.asarray(vec), *params, np.asarray(vec), k * 4]).fetchall()
        hits = [
            SearchHit(chunk_id=r[0], video_id=r[1], title=r[2] or r[1], date=r[3].isoformat(), kind=r[4],
                      section=r[5], start=float(r[6]), end=float(r[7]), text=r[8],
                      score=float(r[9]) + (SUMMARY_BOOST if r[4] == "summary" else 0.0))
            for r in rows
        ]
        hits.sort(key=lambda h: -h.score)
        return _specific_first(hits)[:k]
