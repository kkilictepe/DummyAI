"""``metric_lookup`` — resolve a natural-language phrase to concrete catalog metrics.

Thin ``@tool`` wrapper over a :class:`~src.tools.metric_lookup.resolver.MetricLookupResolver`.
The module-level ``_resolver`` is the single swap point: :func:`_select_resolver` picks the
deterministic v1 resolver by default, or the Phase 6 embedding resolver when
``tools.yaml:embedding_metric_retrieval.enabled`` is true — the tool name / args / callers and the
returned shape are identical either way.
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.config import get_tools_config
from src.logging import get_logger
from src.tools.metric_lookup.resolver import CatalogSearchResolver, MetricLookupResolver

_log = get_logger(__name__)


def _select_resolver() -> MetricLookupResolver:
    """Deterministic resolver by default; the embedding resolver only when explicitly enabled.

    Any failure (config unreadable, optional extra missing) falls back to the deterministic
    resolver — enabling the flag can never break ``metric_lookup``.
    """
    try:
        config = get_tools_config().for_tool("embedding_metric_retrieval")
    except Exception as exc:
        _log.warning("metric_lookup_config_unreadable", error=str(exc))
        return CatalogSearchResolver()

    if not config.get("enabled"):
        return CatalogSearchResolver()

    try:
        from src.tools.embedding_metric_retrieval import build_embedding_resolver

        _log.info("metric_lookup_using_embedding_resolver")
        return build_embedding_resolver()
    except Exception as exc:
        _log.warning("embedding_resolver_unavailable", error=str(exc))
        return CatalogSearchResolver()


# The one place the resolver implementation is chosen (import-time; the flag is a deploy setting).
_resolver: MetricLookupResolver = _select_resolver()


class MetricLookupInput(BaseModel):
    """Arguments for ``metric_lookup``."""

    query: str = Field(
        min_length=1,
        description="Natural-language description of the metric(s) to find, e.g. 'high CPU', "
        "'ABAP short dumps', 'dialog response time'.",
    )
    category: str | None = Field(
        default=None,
        description="Optional catalog category to restrict the search "
        "(e.g. 'error_and_warnings', 'infrastructure_resource_usage').",
    )
    profile: str | None = Field(
        default=None,
        description="Optional metric profile to restrict the search to that profile's metrics "
        "(e.g. 'cpu_overview', 'hana_db_resource_usage').",
    )
    top_k: int = Field(
        default=5,
        ge=1,
        le=25,
        description="Maximum number of candidate metrics to return.",
    )


@tool("metric_lookup", args_schema=MetricLookupInput)
def metric_lookup(
    query: str,
    category: str | None = None,
    profile: str | None = None,
    top_k: int = 5,
) -> dict[str, object]:
    """Find the SAP/HANA metrics that best match a phrase, ranked by relevance.

    Use this to translate a user's words into the exact Prometheus metric names to query. Returns
    ``candidates`` (each with logical_key, prometheus_name, description, category, unit, score)
    and the de-duplicated ordered ``prometheus_names``.
    """
    candidates = _resolver.search(query, category=category, profile=profile, top_k=top_k)

    prometheus_names: list[str] = []
    for candidate in candidates:
        if candidate.prometheus_name not in prometheus_names:
            prometheus_names.append(candidate.prometheus_name)

    return {
        "query": query,
        "candidates": [c.to_dict() for c in candidates],
        "prometheus_names": prometheus_names,
    }
