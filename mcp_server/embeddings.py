"""Local multilingual embeddings (BGE-M3 by default): free, offline after the first download."""

from __future__ import annotations

import os
from functools import lru_cache

DIM = 1024  # must match kb_chunks.embedding in data/schema.sql


def model_name() -> str:
    return os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer  # slow import; load only when needed

    return SentenceTransformer(model_name(), device="cpu")


def embed(texts: list[str]) -> list[list[float]]:
    vectors = _model().encode(texts, normalize_embeddings=True, batch_size=16, show_progress_bar=False)
    if vectors.shape[1] != DIM:
        raise ValueError(f"{model_name()} gives {vectors.shape[1]} dimensions; the schema expects {DIM}")
    return vectors.tolist()


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
