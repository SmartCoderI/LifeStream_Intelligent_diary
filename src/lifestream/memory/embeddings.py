"""Text embeddings. Default: BAAI/bge-small-en-v1.5, a small local retrieval model
(384 dimensions, runs on CPU, no API key)."""

from __future__ import annotations

from typing import Protocol

import numpy as np

# bge models expect this instruction in front of search queries (not documents)
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class LocalEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", device: str = "cpu"):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name, device=device)
        self.dim = int(self._model.get_embedding_dimension())
        self._query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._model.encode(texts, normalize_embeddings=True, batch_size=32,
                                  convert_to_numpy=True).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._model.encode([self._query_prefix + text], normalize_embeddings=True,
                                  convert_to_numpy=True)[0].astype(np.float32)


_cache: dict[str, LocalEmbedder] = {}


def is_loaded(model_name: str) -> bool:
    return model_name in _cache


def get_embedder(model_name: str) -> LocalEmbedder:
    """Load once per process (loading takes several seconds)."""
    if model_name not in _cache:
        _cache[model_name] = LocalEmbedder(model_name)
    return _cache[model_name]
