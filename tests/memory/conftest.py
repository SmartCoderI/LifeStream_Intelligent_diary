import hashlib
import os
import re
import shutil
import subprocess

import numpy as np
import pytest


class FakeEmbedder:
    """Deterministic bag-of-words vectors: fast, no model download."""

    name = "fake-bow"
    dim = 64

    def _vec(self, text):
        v = np.zeros(self.dim, dtype=np.float32)
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts):
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text):
        return self._vec(text)


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="session")
def pg_url():
    """A throwaway Postgres + pgvector container for the whole test session."""
    if not _docker_ok():
        pytest.skip("Docker is not running")
    # The Ryuk cleanup helper can't mount Docker Desktop's socket on macOS; the
    # `with` block below removes the container anyway.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17", driver=None) as pg:
        yield pg.get_connection_url()
