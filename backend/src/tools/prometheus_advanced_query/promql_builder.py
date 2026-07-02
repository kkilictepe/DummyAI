"""Safe PromQL construction from validated components.

Ported from the reference ``SafePromQLBuilder``, trimmed to the surface the :class:`QueryEngine`
actually uses. It NEVER accepts raw PromQL: queries are built programmatically from validated
metric names and label filters, with every label value escaped to prevent breakout/injection.
All methods are stateless and deterministic.
"""

from __future__ import annotations


class SafePromQLBuilder:
    """Builds safe ``metric{label="value", ...}`` selectors from validated inputs."""

    def build_instant_query(self, metric_name: str, labels: dict[str, str] | None = None) -> str:
        """Build a selector for a single metric: ``metric_name{label1="value1", ...}``."""
        label_selector = self._build_label_selector(labels)
        if label_selector:
            return f"{metric_name}{{{label_selector}}}"
        return metric_name

    def build_multi_metric_query(
        self, metric_names: list[str], labels: dict[str, str] | None = None
    ) -> list[tuple[str, str]]:
        """Build one independent ``(metric_name, query)`` selector per metric (safer than joins)."""
        return [
            (metric_name, self.build_instant_query(metric_name, labels))
            for metric_name in metric_names
        ]

    def _build_label_selector(self, labels: dict[str, str] | None) -> str:
        """Build the ``key="value", ...`` body from a label dict (sorted for determinism)."""
        if not labels:
            return ""
        selectors = [
            f'{key}="{self._escape_label_value(value)}"' for key, value in sorted(labels.items())
        ]
        return ", ".join(selectors)

    @staticmethod
    def _escape_label_value(value: str) -> str:
        """Escape a label value for safe inclusion in a double-quoted PromQL string.

        Order matters: escape the backslash first, then the quote, then control chars — so an
        injected ``"}`` cannot break out of the quoted label value.
        """
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        value = value.replace("\n", "\\n")
        value = value.replace("\r", "\\r")
        return value
