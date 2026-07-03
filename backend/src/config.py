"""Configuration for the Dummy AI backend.

Two responsibilities, separated by a **credential firewall**:

1. ``Settings`` (secrets) — sources API keys / tokens / hosts from the process
   environment or ``.env`` as ``SecretStr``, layered over the committed, non-secret
   ``config/config.yaml``. Only client builders and ``main.py`` import ``get_settings()``.

2. Non-secret YAML loaders (``get_systems``, ``get_metric_catalog``,
   ``get_metric_profiles``, ``get_tools_config``) — return typed models built from the
   committed ``config/*.yaml``. **Tools import only these**, never ``get_settings()`` — so a
   tool can never touch a secret. Committed secrets (e.g. ``systems.yaml`` passwords) are
   dropped at the loader boundary and never enter memory.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# ---------------------------------------------------------------------------
# Config directory resolution (overridable for tests via DUMMYAI_CONFIG_DIR)
# ---------------------------------------------------------------------------


def config_dir() -> Path:
    """Directory holding the committed ``*.yaml`` config files."""
    override = os.environ.get("DUMMYAI_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config"


def _load_yaml(filename: str) -> dict[str, Any]:
    path = config_dir() / filename
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


# ---------------------------------------------------------------------------
# Non-secret application config (from config/config.yaml)
# ---------------------------------------------------------------------------


class LLMSettings(BaseModel):
    """LLM model ids + generation knobs (non-secret). OpenAI models (override in config.yaml).

    ``reasoning_effort`` applies only to reasoning models (gpt-5 / o-series) and is forwarded by
    the shared model builder only for those; ``None`` means "use the model's default effort".
    ``temperature`` is honoured by non-reasoning models (gpt-4o) and ignored by gpt-5/o1 (which
    only support their default); ``max_tokens`` maps to ``max_completion_tokens`` and, for a
    reasoning model, is the **combined** reasoning + visible-output budget."""

    answer_model: str = "gpt-4o"
    guard_model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 4096
    # None -> model default effort; guard defaults to the cheapest/fastest for a snap verdict.
    answer_reasoning_effort: str | None = None
    guard_reasoning_effort: str | None = "none"


class CopilotSettings(BaseModel):
    """Copilot flow behaviour."""

    max_tool_iterations: int = 8


class CorsSettings(BaseModel):
    allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


class ElasticsearchSettings(BaseModel):
    """Non-secret ES behaviour (hosts/api_key are separate SecretStr fields)."""

    index_name: str = "sap-logs-*"


# ---------------------------------------------------------------------------
# Settings (secrets + layered non-secret config)
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Runtime settings. Env / ``.env`` override ``config.yaml``; nested via ``SECTION__KEY``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime ---
    environment: str = "development"
    log_level: str = "INFO"

    # --- Prometheus (hosts are not secret; token is) ---
    prometheus_url: str = "http://localhost:9090"
    prometheus_token: SecretStr | None = None
    # Multi-tenant gateway auth (Cortex/Mimir-style + the Portakal proxy in front of the SAP
    # Prometheus). ``prometheus_org_id`` -> ``X-Scope-OrgID`` header; ``prometheus_portakal_token``
    # -> ``X-Portakal-Token`` header. Both optional: a plain single-tenant Prometheus needs neither.
    prometheus_org_id: str | None = None
    prometheus_portakal_token: SecretStr | None = None

    # --- Elasticsearch (hosts not secret; api key is) ---
    elasticsearch_hosts: str = "http://localhost:9200"
    elasticsearch_api_key: SecretStr | None = None

    # --- Langfuse (tracing) ---
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # --- LLM provider (OpenAI) ---
    openai_api_key: SecretStr | None = None

    # --- non-secret app config (populated from config.yaml) ---
    llm: LLMSettings = Field(default_factory=LLMSettings)
    copilot: CopilotSettings = Field(default_factory=CopilotSettings)
    cors: CorsSettings = Field(default_factory=CorsSettings)
    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Priority: init > env > .env > config.yaml > file secrets. Env beats YAML.
        yaml_source = YamlConfigSettingsSource(settings_cls, yaml_file=config_dir() / "config.yaml")
        return (init_settings, env_settings, dotenv_settings, yaml_source, file_secret_settings)


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Imported only by client builders and ``main.py``."""
    return Settings()


# ---------------------------------------------------------------------------
# Non-secret typed models + loaders (the tool-facing side of the firewall)
# ---------------------------------------------------------------------------


class SystemConfig(BaseModel):
    """A managed SAP system. ``password`` is intentionally omitted — committed secrets are
    never loaded. ``host``/``user`` are kept for internal use but never exposed to the LLM/UI
    (see ``tools/systems.py``)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    system_type: str = "SAP"
    display_name: str | None = None
    environment: str | None = None
    host: str | None = None
    sysnr: str | None = None
    client: str | None = None
    user: str | None = None


class MetricCatalogEntry(BaseModel):
    """One row of ``metric_catalog.yaml``: logical key -> Prometheus name + metadata."""

    model_config = ConfigDict(extra="ignore")

    prometheus_name: str
    metric_type: str | None = None
    description: str | None = None
    category: str | None = None
    recommended_operations: str | None = None
    # Catalog mixes '5', '-', empty and missing — keep as str|None and coerce at use-site.
    alert_threshold: str | None = None
    unit: str | None = None

    @field_validator("alert_threshold", mode="before")
    @classmethod
    def _coerce_threshold(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None


# logical profile key -> ordered list of catalog keys
MetricProfiles = dict[str, list[str]]


class ToolsConfig(BaseModel):
    """Per-tool behaviour settings from ``tools.yaml``."""

    model_config = ConfigDict(extra="ignore")

    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def for_tool(self, name: str) -> dict[str, Any]:
        """Return the settings block for ``name`` (empty dict if absent)."""
        return dict(self.tools.get(name, {}))


@lru_cache
def get_systems() -> dict[str, SystemConfig]:
    """Managed systems keyed by id (KHP, KBP, ...). Never carries ``password``."""
    raw = _load_yaml("systems.yaml")
    systems = raw.get("systems") or {}
    out: dict[str, SystemConfig] = {}
    for name, cfg in systems.items():
        data = dict(cfg or {})
        data.pop("password", None)  # defense in depth — never even reaches the model
        out[name] = SystemConfig(name=name, **data)
    return out


@lru_cache
def get_metric_catalog() -> dict[str, MetricCatalogEntry]:
    """logical key -> catalog entry."""
    raw = _load_yaml("metric_catalog.yaml")
    return {key: MetricCatalogEntry(**(val or {})) for key, val in raw.items()}


@lru_cache
def get_metric_profiles() -> MetricProfiles:
    """profile name -> ordered logical keys."""
    raw = _load_yaml("metric_profiles.yaml")
    return {key: list(val or []) for key, val in raw.items()}


@lru_cache
def get_tools_config() -> ToolsConfig:
    """Per-tool settings from ``tools.yaml``."""
    raw = _load_yaml("tools.yaml")
    return ToolsConfig(tools=raw)


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


def validate_config() -> list[str]:
    """Cross-check profiles against the catalog. Returns a list of human-readable warnings
    (empty when consistent). Callers decide whether warnings are fatal."""
    catalog = get_metric_catalog()
    profiles = get_metric_profiles()
    warnings: list[str] = []
    for profile, keys in profiles.items():
        for key in keys:
            if key not in catalog:
                warnings.append(f"profile '{profile}' references unknown metric key '{key}'")
    return warnings


def reset_config_caches() -> None:
    """Clear all cached config (used by tests after changing env / DUMMYAI_CONFIG_DIR)."""
    get_settings.cache_clear()
    get_systems.cache_clear()
    get_metric_catalog.cache_clear()
    get_metric_profiles.cache_clear()
    get_tools_config.cache_clear()
