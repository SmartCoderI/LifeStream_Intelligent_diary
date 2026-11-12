"""F8/F9 with a real Postgres + pgvector (testcontainers): schema, connection,
indexing flow, date filters, plus the retrieval data test and M2 with the real
embedding model."""

import datetime as dt

import pytest

from lifestream.evaluation.retrieval import evaluate, scenario
from lifestream.memory.store import MemoryConfigError, MemoryStore, MemoryUnavailable

from conftest import FakeEmbedder


@pytest.fixture
def store(pg_url, request):
    s = MemoryStore(pg_url, FakeEmbedder(), schema=f"t_{request.node.name[:30].lower()}")
    s.reset()
    yield s
    s.conn.execute(f"DROP SCHEMA {s.schema} CASCADE")
    s.close()


def test_unreachable_database_gives_clear_error():
    with pytest.raises(MemoryUnavailable, match="docker compose up -d db"):
        MemoryStore("postgresql://x:y@127.0.0.1:1/z", FakeEmbedder())


def test_schema_created(store):
    tables = {r[0] for r in store.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (store.schema,))}
    assert tables == {"meta", "videos", "turns", "chunks", "speaker_map"}
    kinds = store.conn.execute(f"SELECT value FROM {store.schema}.meta ORDER BY key").fetchall()
    assert [k[0] for k in kinds] == ["64", "fake-bow"]


def test_index_flow_and_reindex_replaces(store):
    summary, script = scenario()[0]
    n = store.index_video(summary, script, {"Maya": "Maya L."})
    assert store.count() == {"videos": 1, "chunks": n}
    n2 = store.index_video(summary, script)
    assert store.count() == {"videos": 1, "chunks": n2}  # no duplicates
    turns = store.conn.execute(f"SELECT count(*) FROM {store.schema}.turns").fetchone()[0]
    assert turns == len(script)


def test_names_are_applied_and_stored(store):
    from lifestream.schemas import ScriptSegment
    summary, _ = scenario()[-1]  # vlog, speaker SPEAKER_00
    script = [ScriptSegment(start=0, end=10, speaker="SPEAKER_00", text="starting the hike")]
    store.index_video(summary, script, {"SPEAKER_00": "Dana"})
    text = store.conn.execute(f"SELECT text FROM {store.schema}.chunks WHERE kind='transcript'").fetchone()[0]
    assert "Dana: starting the hike" in text
    rows = store.conn.execute(f"SELECT speaker_id, real_name FROM {store.schema}.speaker_map").fetchall()
    assert rows == [("SPEAKER_00", "Dana")]


def test_date_filter_and_delete(store):
    for summary, script in scenario():
        store.index_video(summary, script)
    hits = store.search("frontend navigation", k=20, date_from=dt.date(2026, 11, 2), date_to=dt.date(2026, 11, 8))
    assert hits and all("2026-11-02" <= h.date <= "2026-11-08" for h in hits)
    assert "frontend-kickoff" not in {h.video_id for h in hits}  # 2026-10-21
    store.delete_video("frontend-sync")
    assert "frontend-sync" not in store.indexed_videos()


def test_embedding_model_change_is_detected(pg_url, store):
    class Other(FakeEmbedder):
        name = "other-model"
    with pytest.raises(MemoryConfigError, match="--rebuild"):
        MemoryStore(pg_url, Other(), schema=store.schema)


def test_retrieval_accuracy_and_m2_with_real_embeddings(pg_url):
    """F9 data test: generated videos + questions with known answers (real bge model)."""
    from lifestream.memory.embeddings import get_embedder
    s = MemoryStore(pg_url, get_embedder("BAAI/bge-small-en-v1.5"), schema="t_retrieval")
    try:
        report = evaluate(s, k=3)
    finally:
        s.conn.execute("DROP SCHEMA t_retrieval CASCADE")
        s.close()
    print(f"recall@3={report.recall_at_k:.0%} MRR={report.mrr:.2f} M2 top={report.m2_top}")
    assert report.m2_passed, report.m2_top
    assert report.recall_at_k >= 0.9
    assert report.mrr >= 0.8


def test_index_and_search_time_embedding_and_database_separately(store):
    from lifestream.timing import Timings
    summary, script = scenario()[0]
    t = Timings()
    with t.step("index", "Add to search"):
        store.index_video(summary, script, timings=t)
    with t.step("search", "Search"):
        store.search("frontend", k=3, timings=t)
    names = {s["name"]: s for s in t.spans}
    assert {"index.embed", "index.db", "search.embed", "search.db"} <= set(names)
    assert names["index.embed"]["parent"] == "index" and names["search.db"]["parent"] == "search"
    assert names["index.embed"]["meta"]["chunks"] > 0
