"""Metric resolution: the ``MetricLookupResolver`` seam + the deterministic v1 implementation.

The Copilot needs to turn a natural-language phrase ("high CPU", "short dumps") into concrete
catalog metrics. v1 does this **deterministically** with token/substring overlap over the
committed catalog — zero ML dependencies. Phase 6 swaps in a FAISS + OpenAI-embedding resolver
that implements the **same** :class:`MetricLookupResolver` protocol, so the ``metric_lookup``
tool, its args, and every caller stay byte-for-byte identical.

Scoring is a weighted overlap between the (synonym-expanded, stopword-stripped) query tokens and
each catalog entry's fields. Logical key and category are weighted highest (they are curated),
the Prometheus name next, and the free-text description lowest. Ties break on the logical key so
results are **stable** across runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from src.config import MetricCatalogEntry
from src.tools._catalog import MetricCatalog

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Field weights: curated fields (key, category) outrank the free-text description.
_WEIGHT_KEY = 3.0
_WEIGHT_CATEGORY = 2.0
_WEIGHT_NAME = 1.5
_WEIGHT_DESCRIPTION = 1.0
# Bonus when the full multi-word query appears verbatim in the description.
_PHRASE_BONUS = 2.0

# Query-only noise words — stripped before scoring so "how is cpu on KHP" keys on "cpu".
_STOPWORDS = frozenset(
    {
        "the",
        "is",
        "are",
        "was",
        "were",
        "a",
        "an",
        "of",
        "on",
        "in",
        "for",
        "to",
        "and",
        "or",
        "how",
        "what",
        "why",
        "show",
        "me",
        "my",
        "any",
        "with",
        "at",
        "by",
        "it",
        "this",
        "that",
        "have",
        "has",
        "do",
        "does",
        "can",
        "please",
        "give",
        "get",
        "there",
    }
)

# Conservative synonym expansion drawn from the reference keyword map: maps a query token to
# extra tokens so a user's word reaches the catalog's vocabulary (e.g. "ram" -> "memory").
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "ram": ("memory",),
    "processor": ("cpu",),
    "oom": ("memory",),
    "disk": ("disc",),
    "disc": ("disk",),
    "wp": ("work", "process"),
    "workprocess": ("work", "process"),
    "dump": ("shortdumps",),
    "dumps": ("shortdumps",),
}


def _tokenize(text: str) -> set[str]:
    """Lowercase and split into alphanumeric tokens of length >= 2."""
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) >= 2}


def _query_tokens(query: str) -> set[str]:
    """Tokenize a query: drop stopwords, then add any synonym expansions."""
    tokens = {tok for tok in _tokenize(query) if tok not in _STOPWORDS}
    expanded = set(tokens)
    for tok in tokens:
        expanded.update(_SYNONYMS.get(tok, ()))
    return expanded


@dataclass(frozen=True)
class MetricCandidate:
    """One scored catalog match returned by a resolver."""

    logical_key: str
    prometheus_name: str
    description: str | None
    category: str | None
    unit: str | None
    score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_key": self.logical_key,
            "prometheus_name": self.prometheus_name,
            "description": self.description,
            "category": self.category,
            "unit": self.unit,
            "score": self.score,
        }


class MetricLookupResolver(Protocol):
    """The stable seam. The deterministic resolver and the future FAISS resolver both satisfy it,
    so ``metric_lookup``'s tool name / args / callers never change when the implementation swaps."""

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        profile: str | None = None,
        top_k: int = 5,
    ) -> list[MetricCandidate]: ...


class CatalogSearchResolver:
    """Deterministic v1 resolver — weighted token/substring overlap over the metric catalog."""

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        profile: str | None = None,
        top_k: int = 5,
    ) -> list[MetricCandidate]:
        catalog = MetricCatalog.load()

        # 1. Candidate pool: a known profile restricts the pool to its keys; otherwise all keys.
        if profile and catalog.has_profile(profile):
            keys = catalog.profile_keys(profile)
        else:
            keys = catalog.keys()
        pool: list[tuple[str, MetricCatalogEntry | None]] = [(k, catalog.entry(k)) for k in keys]

        # 2. Optional category filter (matched loosely against the entry's catalog category).
        if category:
            cat = category.lower()
            pool = [
                (k, e)
                for k, e in pool
                if e is not None
                and e.category is not None
                and (cat in e.category.lower() or e.category.lower() in cat)
            ]

        tokens = _query_tokens(query)

        scored: list[MetricCandidate] = []
        for key, entry in pool:
            if entry is None:
                continue
            score = self._score(key, entry, tokens, query)
            # With no usable query tokens, surface the (already filtered) pool at score 0 so a
            # bare ``profile=`` / ``category=`` lookup still returns that group's metrics.
            if score > 0 or not tokens:
                scored.append(
                    MetricCandidate(
                        logical_key=key,
                        prometheus_name=entry.prometheus_name,
                        description=entry.description,
                        category=entry.category,
                        unit=entry.unit,
                        score=round(score, 3),
                    )
                )

        # 3. Stable ranking: score desc, then logical key asc (so equal scores never reorder).
        scored.sort(key=lambda c: (-c.score, c.logical_key))
        return scored[: max(0, top_k)]

    @staticmethod
    def _score(key: str, entry: MetricCatalogEntry, tokens: set[str], raw_query: str) -> float:
        if not tokens:
            return 0.0

        key_tokens = _tokenize(key)
        name_tokens = _tokenize(entry.prometheus_name)
        category_tokens = _tokenize(entry.category or "")
        description = (entry.description or "").lower()
        description_tokens = _tokenize(description)

        score = 0.0
        for tok in tokens:
            if tok in key_tokens:
                score += _WEIGHT_KEY
            if tok in category_tokens:
                score += _WEIGHT_CATEGORY
            if tok in name_tokens:
                score += _WEIGHT_NAME
            if tok in description_tokens:
                score += _WEIGHT_DESCRIPTION

        # Whole-phrase substring hit in the description is a strong signal.
        normalized = " ".join(_TOKEN_RE.findall(raw_query.lower()))
        if len(normalized) > 2 and " " in normalized and normalized in description:
            score += _PHRASE_BONUS
        return score
