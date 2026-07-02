"""Embedding backend seam.

The resolver depends only on the :class:`EmbeddingBackend` protocol, so tests inject a fake and
production uses OpenAI. ``langchain_openai`` is imported lazily (deferring client construction to
first use) and reads ``OPENAI_API_KEY`` from the environment itself — no secret ever flows through
the tool layer.
"""

from __future__ import annotations

from typing import Any, Protocol


class EmbeddingBackend(Protocol):
    """Turns text into vectors. Both methods may raise (no key / no network); the resolver treats
    any failure as "no semantic signal" and degrades to keyword+rule scoring."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbeddingBackend:
    """Default backend over ``langchain_openai.OpenAIEmbeddings`` (lazy-imported at first use)."""

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self._model = model
        self._client: Any = None

    def _lazy_client(self) -> Any:
        if self._client is None:
            from langchain_openai import OpenAIEmbeddings

            self._client = OpenAIEmbeddings(model=self._model)
        return self._client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(vec) for vec in self._lazy_client().embed_documents(texts)]

    def embed_query(self, text: str) -> list[float]:
        return list(self._lazy_client().embed_query(text))


__all__ = ["EmbeddingBackend", "OpenAIEmbeddingBackend"]
