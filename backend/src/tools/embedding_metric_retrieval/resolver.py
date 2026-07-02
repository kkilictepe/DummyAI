"""``EmbeddingMetricResolver`` — the Phase 6 semantic implementation of the metric-lookup seam.

Satisfies the same :class:`~src.tools.metric_lookup.resolver.MetricLookupResolver` protocol as the
deterministic v1 resolver, so ``metric_lookup``'s tool name / args / callers are unchanged — only
the module-level ``_resolver`` in ``metric_lookup/tool.py`` swaps (behind a config flag).

Hybrid score per catalog entry (weights sum to 1.0):

    final = 0.60 * embedding  +  0.25 * keyword  +  0.15 * rule

- **embedding** — cosine similarity of the query vs the entry's document (semantic recall).
- **keyword** — fraction of query tokens that hit the entry's *curated* fields (key / name /
  category): precise, vocabulary-exact matches.
- **rule** — synonym-expanded + description overlap + a verbatim-phrase bonus: softer signals.

Every component is normalized to ``[0, 1]``. If the embedding backend is unavailable (missing
extra, no ``OPENAI_API_KEY``, transport error) the embedding term is simply 0 and the resolver
still ranks by ``keyword``/``rule`` — ``metric_lookup`` never breaks.
"""

from __future__ import annotations

from src.logging import get_logger
from src.tools._catalog import MetricCatalog
from src.tools.embedding_metric_retrieval.embeddings import EmbeddingBackend
from src.tools.embedding_metric_retrieval.index import CatalogEmbeddingIndex
from src.tools.metric_lookup.resolver import (
    _STOPWORDS,
    _TOKEN_RE,
    MetricCandidate,
    _tokenize,
)
from src.tools.metric_lookup.resolver import (
    _query_tokens as _base_and_synonym_tokens,
)

_log = get_logger(__name__)

_EMBEDDING_WEIGHT = 0.60
_KEYWORD_WEIGHT = 0.25
_RULE_WEIGHT = 0.15
_PHRASE_BONUS = 0.3


def _base_tokens(query: str) -> set[str]:
    """Stopword-stripped query tokens, WITHOUT synonym expansion (the keyword component)."""
    return {tok for tok in _tokenize(query) if tok not in _STOPWORDS}


def _curated_tokens(key: str, prometheus_name: str, category: str | None) -> set[str]:
    return _tokenize(key) | _tokenize(prometheus_name) | _tokenize(category or "")


def _keyword_score(base_tokens: set[str], curated: set[str]) -> float:
    """Fraction of (stopword-stripped) query tokens that appear in the entry's curated fields."""
    if not base_tokens:
        return 0.0
    return len(base_tokens & curated) / len(base_tokens)


def _rule_score(
    query: str,
    base_tokens: set[str],
    curated: set[str],
    description: str,
) -> float:
    """Synonym-expanded overlap across all fields + a verbatim whole-phrase bonus."""
    expanded = _base_and_synonym_tokens(query)  # base tokens + synonym expansions
    if not expanded:
        return 0.0
    all_fields = curated | _tokenize(description)
    score = len(expanded & all_fields) / len(expanded)

    normalized_phrase = " ".join(_TOKEN_RE.findall(query.lower()))
    if (
        len(normalized_phrase) > 2
        and " " in normalized_phrase
        and normalized_phrase in description.lower()
    ):
        score += _PHRASE_BONUS
    return min(1.0, score)


class EmbeddingMetricResolver:
    """Semantic + lexical hybrid resolver over the metric catalog."""

    def __init__(
        self,
        backend: EmbeddingBackend,
        *,
        embedding_weight: float = _EMBEDDING_WEIGHT,
        keyword_weight: float = _KEYWORD_WEIGHT,
        rule_weight: float = _RULE_WEIGHT,
    ) -> None:
        self._index = CatalogEmbeddingIndex(backend)
        self._w_embedding = embedding_weight
        self._w_keyword = keyword_weight
        self._w_rule = rule_weight

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        profile: str | None = None,
        top_k: int = 5,
    ) -> list[MetricCandidate]:
        catalog = MetricCatalog.load()

        # Candidate pool: a known profile restricts to its keys; else all keys.
        if profile and catalog.has_profile(profile):
            keys = catalog.profile_keys(profile)
        else:
            keys = catalog.keys()

        # Optional loose category filter (mirrors the deterministic resolver).
        if category:
            cat = category.lower()
            keys = [
                k
                for k in keys
                if (e := catalog.entry(k)) is not None
                and e.category is not None
                and (cat in e.category.lower() or e.category.lower() in cat)
            ]

        base_tokens = _base_tokens(query)
        similarities = self._safe_similarities(query, catalog)

        scored: list[MetricCandidate] = []
        for key in keys:
            entry = catalog.entry(key)
            if entry is None:
                continue
            curated = _curated_tokens(key, entry.prometheus_name, entry.category)
            keyword = _keyword_score(base_tokens, curated)
            rule = _rule_score(query, base_tokens, curated, entry.description or "")
            embedding = similarities.get(key, 0.0)
            final = self._w_embedding * embedding + self._w_keyword * keyword + self._w_rule * rule
            # Keep positive matches; a token-less query (bare profile/category lookup) still
            # surfaces the filtered pool at score 0 — same contract as the deterministic resolver.
            if final > 0 or not base_tokens:
                scored.append(
                    MetricCandidate(
                        logical_key=key,
                        prometheus_name=entry.prometheus_name,
                        description=entry.description,
                        category=entry.category,
                        unit=entry.unit,
                        score=round(final, 4),
                    )
                )

        # Stable ranking: score desc, then logical key asc (equal scores never reorder).
        scored.sort(key=lambda c: (-c.score, c.logical_key))
        return scored[: max(0, top_k)]

    def _safe_similarities(self, query: str, catalog: MetricCatalog) -> dict[str, float]:
        """Embedding similarities, or ``{}`` if the backend is unavailable (degrade gracefully)."""
        try:
            return self._index.similarities(query, catalog)
        except Exception as exc:
            _log.warning("embedding_similarities_unavailable", error=str(exc))
            return {}


__all__ = ["EmbeddingMetricResolver"]
