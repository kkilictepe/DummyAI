"""Pure-python cosine index over the metric catalog (the FAISS analog for a tiny catalog).

The catalog is ~50 entries, so a brute-force cosine over normalized vectors is exact and
dependency-free — no ``faiss``/``numpy``. If the catalog ever grew to thousands of entries, a real
ANN index could drop in behind this same class without touching the resolver.

Each catalog entry becomes one :class:`MetricDocument` (its "knowledge unit"): a semantic text
built from the curated fields, embedded once and cached until the catalog keys change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.tools._catalog import MetricCatalog
from src.tools.embedding_metric_retrieval.embeddings import EmbeddingBackend


@dataclass(frozen=True)
class MetricDocument:
    """One indexed catalog entry: its logical key + the text that gets embedded."""

    logical_key: str
    text: str


def build_document_text(catalog: MetricCatalog, key: str) -> str:
    """Assemble the embedding text for a catalog entry from its curated fields."""
    entry = catalog.entry(key)
    if entry is None:
        return key
    parts = [
        key,
        entry.prometheus_name,
        entry.category or "",
        entry.recommended_operations or "",
        entry.description or "",
    ]
    return " ".join(p for p in parts if p)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    # Both operands are already L2-normalized, so the dot product IS the cosine similarity.
    return sum(x * y for x, y in zip(a, b, strict=True))


class CatalogEmbeddingIndex:
    """Embeds catalog documents once and answers cosine-similarity queries.

    The embedded vectors are cached keyed by the tuple of catalog keys, so a config reload that
    changes the catalog (tests re-pointing ``DUMMYAI_CONFIG_DIR``) transparently rebuilds. Any
    backend failure propagates to the caller, which decides how to degrade.
    """

    def __init__(self, backend: EmbeddingBackend) -> None:
        self._backend = backend
        self._keys: tuple[str, ...] | None = None
        self._vectors: dict[str, list[float]] = {}

    def _ensure(self, catalog: MetricCatalog) -> None:
        keys = tuple(catalog.keys())
        if self._keys == keys and self._vectors:
            return
        docs = [build_document_text(catalog, k) for k in keys]
        raw = self._backend.embed_documents(docs)
        self._vectors = {k: _normalize(list(v)) for k, v in zip(keys, raw, strict=True)}
        self._keys = keys

    def similarities(self, query: str, catalog: MetricCatalog) -> dict[str, float]:
        """Return ``{logical_key: cosine_similarity in [0, 1]}`` for every catalog entry.

        Similarities are clamped at 0 (negative cosine = unrelated, contributes nothing to the
        weighted score). Raises whatever the backend raises — the caller handles degradation.
        """
        self._ensure(catalog)
        query_vec = _normalize(list(self._backend.embed_query(query)))
        return {key: max(0.0, _cosine(query_vec, vec)) for key, vec in self._vectors.items()}


__all__ = ["CatalogEmbeddingIndex", "MetricDocument", "build_document_text"]
