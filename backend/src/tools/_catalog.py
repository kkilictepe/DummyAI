"""Shared read model over the metric catalog + profiles.

Both metric tools (``metric_lookup`` and ``tool_sap_metric_categorized``) need the same two
things: the logical-key → :class:`~src.config.MetricCatalogEntry` mapping and the profile →
ordered-keys mapping. :class:`MetricCatalog` wraps the (already ``lru_cache``-d) config loaders
behind one small interface so the two tools cannot drift in how they read the catalog.

Construction is cheap — :meth:`MetricCatalog.load` just references the cached loaders — so tools
build a fresh wrapper per call. That keeps tests correct: a test that re-points
``DUMMYAI_CONFIG_DIR`` and calls ``reset_config_caches()`` is observed on the next ``load()``.
"""

from __future__ import annotations

from src.config import MetricCatalogEntry, get_metric_catalog, get_metric_profiles


class MetricCatalog:
    """Immutable view over ``metric_catalog.yaml`` + ``metric_profiles.yaml``."""

    def __init__(
        self,
        entries: dict[str, MetricCatalogEntry],
        profiles: dict[str, list[str]],
    ) -> None:
        self._entries = entries
        self._profiles = profiles

    @classmethod
    def load(cls) -> MetricCatalog:
        """Build from the cached config loaders (the tool-facing side of the firewall)."""
        return cls(get_metric_catalog(), get_metric_profiles())

    # -- catalog entries ------------------------------------------------

    def entry(self, key: str) -> MetricCatalogEntry | None:
        """Return the catalog entry for a logical key, or ``None`` if unknown."""
        return self._entries.get(key)

    def prometheus_name(self, key: str) -> str | None:
        """Return the Prometheus metric name for a logical key, or ``None`` if unknown."""
        entry = self._entries.get(key)
        return entry.prometheus_name if entry else None

    def keys(self) -> list[str]:
        """All logical keys, in catalog order."""
        return list(self._entries)

    def items(self) -> list[tuple[str, MetricCatalogEntry]]:
        """All ``(logical_key, entry)`` pairs, in catalog order."""
        return list(self._entries.items())

    # -- profiles -------------------------------------------------------

    def has_profile(self, profile: str) -> bool:
        return profile in self._profiles

    def profiles(self) -> list[str]:
        """All profile / category names."""
        return list(self._profiles)

    def profile_keys(self, profile: str) -> list[str]:
        """Ordered logical keys for a profile (empty list if the profile is unknown)."""
        return list(self._profiles.get(profile, []))
