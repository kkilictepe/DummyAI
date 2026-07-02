"""Phase 6 semantic metric retrieval — the FAISS-analog embedding resolver for ``metric_lookup``.

``build_embedding_resolver()`` is the single construction point ``metric_lookup/tool.py`` calls
when ``tools.yaml:embedding_metric_retrieval.enabled`` is true. Construction is cheap (no network):
the OpenAI backend is lazy, so the actual embedding work happens on the first ``search()`` call.
"""

from __future__ import annotations

from src.config import get_tools_config
from src.tools.embedding_metric_retrieval.embeddings import (
    EmbeddingBackend,
    OpenAIEmbeddingBackend,
)
from src.tools.embedding_metric_retrieval.resolver import EmbeddingMetricResolver


def build_embedding_resolver() -> EmbeddingMetricResolver:
    """Construct the embedding resolver from ``tools.yaml`` (OpenAI backend; env-supplied key)."""
    config = get_tools_config().for_tool("embedding_metric_retrieval")
    model = str(config.get("embedding_model", "text-embedding-3-small"))
    return EmbeddingMetricResolver(OpenAIEmbeddingBackend(model))


__all__ = [
    "EmbeddingBackend",
    "EmbeddingMetricResolver",
    "OpenAIEmbeddingBackend",
    "build_embedding_resolver",
]
