"""Phase 6 embedding resolver: hybrid ranking, graceful fallback, filters, cosine, flag wiring.

No real OpenAI/network: a fake embedding backend maps text to token-count vectors so cosine is
deterministic and meaningful.
"""

from __future__ import annotations

import pytest

from src.tools.embedding_metric_retrieval import build_embedding_resolver
from src.tools.embedding_metric_retrieval.embeddings import OpenAIEmbeddingBackend
from src.tools.embedding_metric_retrieval.index import (
    CatalogEmbeddingIndex,
    _cosine,
    _normalize,
    build_document_text,
)
from src.tools.embedding_metric_retrieval.resolver import EmbeddingMetricResolver
from src.tools.metric_lookup.resolver import CatalogSearchResolver, MetricCandidate

_VOCAB = ("cpu", "memory", "dump", "disk", "disc", "dialog", "rfc", "network", "spool")


class FakeBackend:
    """Deterministic token-count embeddings — no network. Counts calls for cache assertions."""

    def __init__(self) -> None:
        self.doc_calls = 0
        self.query_calls = 0

    def _vec(self, text: str) -> list[float]:
        low = text.lower()
        return [float(low.count(word)) for word in _VOCAB]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vec(text)


class RaisingBackend:
    """Simulates a missing OPENAI_API_KEY / offline embedding backend."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no OPENAI_API_KEY")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("no OPENAI_API_KEY")


class QueryRaisingBackend(FakeBackend):
    """Index builds fine, but per-query embedding fails — the realistic transient case."""

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("transient embedding failure")


# ---------------------------------------------------------------------------
# Resolver — hybrid ranking
# ---------------------------------------------------------------------------


def test_hybrid_ranks_cpu_metrics_first() -> None:
    resolver = EmbeddingMetricResolver(FakeBackend())
    out = resolver.search("high cpu", top_k=3)
    assert all(isinstance(c, MetricCandidate) for c in out)
    assert out[0].logical_key in {"hana_cpu_utilisation", "sap_application_cpu_utilisation"}
    # Scores are in [0, 1] and sorted descending.
    assert all(0.0 <= c.score <= 1.0 for c in out)
    assert out[0].score >= out[-1].score


def test_stable_tiebreak_on_equal_score() -> None:
    # Both CPU metrics score identically; the logical-key tiebreak puts 'hana_...' first, always.
    out = EmbeddingMetricResolver(FakeBackend()).search("cpu", top_k=2)
    assert out[0].logical_key == "hana_cpu_utilisation"
    assert out[0].score == out[1].score


def test_embedding_surfaces_description_only_match() -> None:
    # The catalog key/name use 'disc' but the description says 'Disk'; the semantic vector still
    # ranks the HANA disc metric for a 'disk' query.
    out = EmbeddingMetricResolver(FakeBackend()).search("disk", top_k=5)
    assert "hana_disc_utilization_percent" in [c.prometheus_name for c in out]


def test_document_index_is_built_once_and_cached() -> None:
    backend = FakeBackend()
    resolver = EmbeddingMetricResolver(backend)
    resolver.search("cpu")
    resolver.search("memory")
    assert backend.doc_calls == 1  # catalog embedded once, reused across searches
    assert backend.query_calls == 2  # one query embed per search


# ---------------------------------------------------------------------------
# Resolver — graceful degradation
# ---------------------------------------------------------------------------


def test_falls_back_to_lexical_when_embeddings_fail() -> None:
    resolver = EmbeddingMetricResolver(RaisingBackend())
    out = resolver.search("high cpu", top_k=3)
    # Still returns sensible candidates ranked by keyword+rule (embedding term is 0).
    assert out
    assert out[0].logical_key in {"hana_cpu_utilisation", "sap_application_cpu_utilisation"}
    # With embedding=0, the max possible score is 0.25 (keyword) + 0.15 (rule) = 0.40.
    assert all(c.score <= 0.40 + 1e-9 for c in out)


def test_no_match_returns_empty_even_without_embeddings() -> None:
    out = EmbeddingMetricResolver(RaisingBackend()).search("zzznonsense", top_k=5)
    assert out == []


def test_partial_failure_query_embed_raises_still_degrades() -> None:
    # Index builds successfully, then the per-query embed raises: _safe_similarities swallows it and
    # the search still returns keyword+rule-ranked candidates without propagating the exception.
    out = EmbeddingMetricResolver(QueryRaisingBackend()).search("high cpu", top_k=3)
    assert out
    assert out[0].logical_key in {"hana_cpu_utilisation", "sap_application_cpu_utilisation"}


@pytest.mark.parametrize(
    "query",
    ["cpu", "high cpu", "cpu utilization", "memory heap usage", "sap", "show me the"],
)
def test_scores_stay_within_unit_range(query: str) -> None:
    # final = 0.6*emb + 0.25*kw + 0.15*rule with each term in [0,1] must never exceed 1.0 or go
    # negative, even when the verbatim-phrase bonus fires ('cpu utilization' is a real description).
    for candidate in EmbeddingMetricResolver(FakeBackend()).search(query, top_k=25):
        assert 0.0 <= candidate.score <= 1.0


# ---------------------------------------------------------------------------
# Resolver — profile / category / top_k (same contract as the deterministic resolver)
# ---------------------------------------------------------------------------


def test_profile_restricts_pool() -> None:
    out = EmbeddingMetricResolver(FakeBackend()).search("cpu", profile="hana_db_resource_usage")
    assert [c.prometheus_name for c in out] == ["hana_cpu_utilization_percent"]


def test_category_filter_restricts_pool() -> None:
    out = EmbeddingMetricResolver(FakeBackend()).search("memory", category="hana_db_resource_usage")
    assert [c.prometheus_name for c in out] == ["hana_memory_utilization_percent"]


def test_top_k_is_capped() -> None:
    out = EmbeddingMetricResolver(FakeBackend()).search("sap", top_k=3)
    assert len(out) <= 3


def test_stopword_only_query_seeds_from_profile() -> None:
    # A token-less query still surfaces the filtered profile pool (score 0) — same contract as the
    # deterministic resolver, and independent of whether embeddings are available.
    out = EmbeddingMetricResolver(RaisingBackend()).search(
        "show me the", profile="hana_db_resource_usage"
    )
    assert {c.prometheus_name for c in out} == {
        "hana_cpu_utilization_percent",
        "hana_disc_utilization_percent",
        "hana_memory_utilization_percent",
    }
    assert all(c.score == 0 for c in out)


# ---------------------------------------------------------------------------
# Index primitives
# ---------------------------------------------------------------------------


def test_normalize_and_cosine() -> None:
    assert _normalize([0.0, 0.0]) == [0.0, 0.0]  # zero vector stays zero (no div-by-zero)
    unit = _normalize([3.0, 4.0])
    assert abs(sum(x * x for x in unit) - 1.0) < 1e-9
    assert _cosine(_normalize([1.0, 0.0]), _normalize([0.0, 1.0])) == 0.0  # orthogonal
    assert abs(_cosine(_normalize([1.0, 1.0]), _normalize([1.0, 1.0])) - 1.0) < 1e-9  # identical


def test_similarities_clamped_and_keyed_by_logical_key() -> None:
    from src.tools._catalog import MetricCatalog

    index = CatalogEmbeddingIndex(FakeBackend())
    sims = index.similarities("cpu", MetricCatalog.load())
    assert "hana_cpu_utilisation" in sims
    assert all(0.0 <= v <= 1.0 for v in sims.values())  # negative cosine clamped to 0


def test_document_text_includes_curated_fields() -> None:
    from src.tools._catalog import MetricCatalog

    text = build_document_text(MetricCatalog.load(), "hana_cpu_utilisation")
    assert "hana_cpu_utilisation" in text
    assert "hana_cpu_utilization_percent" in text
    assert "hana_db_resource_usage" in text


# ---------------------------------------------------------------------------
# Config-flag wiring in metric_lookup/tool.py
# ---------------------------------------------------------------------------


class _FakeToolsConfig:
    def __init__(self, block: dict[str, object]) -> None:
        self._block = block

    def for_tool(self, name: str) -> dict[str, object]:
        return dict(self._block)


def test_select_resolver_defaults_to_deterministic() -> None:
    from src.tools.metric_lookup import tool as tool_mod

    assert isinstance(tool_mod._select_resolver(), CatalogSearchResolver)


def test_select_resolver_uses_embedding_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.tools.metric_lookup import tool as tool_mod

    monkeypatch.setattr(tool_mod, "get_tools_config", lambda: _FakeToolsConfig({"enabled": True}))
    assert isinstance(tool_mod._select_resolver(), EmbeddingMetricResolver)


def test_select_resolver_falls_back_when_config_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If reading the flag itself raises (config store down / malformed tools.yaml), selection must
    # still return the deterministic resolver — _select_resolver runs at import, so an escaping
    # exception would crash `import metric_lookup` entirely (invariant 1).
    from src.tools.metric_lookup import tool as tool_mod

    def _boom() -> object:
        raise RuntimeError("tools.yaml unreadable")

    monkeypatch.setattr(tool_mod, "get_tools_config", _boom)
    assert isinstance(tool_mod._select_resolver(), CatalogSearchResolver)


def test_select_resolver_falls_back_on_build_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.tools.embedding_metric_retrieval as emr
    from src.tools.metric_lookup import tool as tool_mod

    monkeypatch.setattr(tool_mod, "get_tools_config", lambda: _FakeToolsConfig({"enabled": True}))

    def _boom() -> EmbeddingMetricResolver:
        raise RuntimeError("faiss missing")

    monkeypatch.setattr(emr, "build_embedding_resolver", _boom)
    assert isinstance(tool_mod._select_resolver(), CatalogSearchResolver)


def test_build_embedding_resolver_reads_model_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.tools.embedding_metric_retrieval as emr

    monkeypatch.setattr(
        emr, "get_tools_config", lambda: _FakeToolsConfig({"embedding_model": "custom-model"})
    )
    resolver = build_embedding_resolver()
    assert isinstance(resolver, EmbeddingMetricResolver)
    backend = resolver._index._backend
    assert isinstance(backend, OpenAIEmbeddingBackend)
    assert backend._model == "custom-model"
