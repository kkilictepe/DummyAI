"""Config layering, credential firewall, and typed-loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from src.config import (
    Settings,
    get_metric_catalog,
    get_metric_profiles,
    get_settings,
    get_systems,
    get_tools_config,
    reset_config_caches,
    validate_config,
)


def test_config_yaml_is_loaded() -> None:
    s = get_settings()
    # Values come from config/config.yaml, not hardcoded defaults only.
    assert s.llm.answer_model == "gpt-5.5"
    assert s.llm.guard_model == "gpt-5.4-mini"
    assert "http://localhost:5173" in s.cors.allow_origins
    assert s.elasticsearch.index_name == "sap-logs-*"
    assert "Markdown" in s.copilot.system_prompt


def test_llm_reasoning_effort_and_budget_from_yaml() -> None:
    s = get_settings()
    # gpt-5.5 answerer reasons at its default depth; the guard runs 'none' for a snap verdict.
    assert s.llm.answer_reasoning_effort is None
    assert s.llm.guard_reasoning_effort == "none"
    # max_completion_tokens budget leaves headroom for reasoning + a full Markdown answer.
    assert s.llm.max_tokens == 8192


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM__ANSWER_MODEL", "gpt-4.1")
    reset_config_caches()
    assert get_settings().llm.answer_model == "gpt-4.1"


def test_nested_section_key_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM__TEMPERATURE", "0.7")
    reset_config_caches()
    assert get_settings().llm.temperature == pytest.approx(0.7)


def test_secret_is_secretstr_and_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-123")
    reset_config_caches()
    s = get_settings()
    assert isinstance(s.openai_api_key, SecretStr)
    assert s.openai_api_key.get_secret_value() == "sk-secret-123"
    # The raw secret must never appear in repr/str.
    assert "sk-secret-123" not in repr(s)
    assert "sk-secret-123" not in str(s.openai_api_key)


def test_es_flat_secret_and_nested_config_do_not_collide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOSTS", "https://es.example:9200")
    monkeypatch.setenv("ELASTICSEARCH_API_KEY", "es-key")
    reset_config_caches()
    s = get_settings()
    assert s.elasticsearch_hosts == "https://es.example:9200"
    assert isinstance(s.elasticsearch_api_key, SecretStr)
    # Nested app config is untouched by the flat secret fields.
    assert s.elasticsearch.index_name == "sap-logs-*"


def test_systems_never_expose_password() -> None:
    systems = get_systems()
    assert set(systems) >= {"KHP", "KBP", "PROD01"}
    khp = systems["KHP"]
    assert khp.display_name == "SAP KHP"
    assert khp.environment == "production"
    # password is neither a field nor an attribute — committed secrets never load.
    assert "password" not in type(khp).model_fields
    assert not hasattr(khp, "password")
    dumped = khp.model_dump()
    assert "password" not in dumped


def test_metric_catalog_alert_threshold_stays_str_or_none() -> None:
    catalog = get_metric_catalog()
    # numeric-looking threshold stays a string
    assert catalog["sap_application_abap_shortdumps_frequency"].alert_threshold == "5"
    # empty threshold coerces to None
    assert catalog["sap_application_memory_heap_total"].alert_threshold is None
    # sentinel '-' is preserved (use-site decides how to treat it)
    assert catalog["sap_application_bgrfc_inbound_throughput_actual"].alert_threshold == "-"


def test_metric_profiles_all_resolve_in_catalog() -> None:
    profiles = get_metric_profiles()
    catalog = get_metric_catalog()
    assert "cpu_overview" in profiles
    for keys in profiles.values():
        for key in keys:
            assert key in catalog
    # The dedicated validator agrees.
    assert validate_config() == []


def test_tools_config_for_tool() -> None:
    cfg = get_tools_config()
    adv = cfg.for_tool("prometheus_advance_query")
    assert adv["max_metrics_per_call"] == 10
    assert cfg.for_tool("does_not_exist") == {}


def test_config_dir_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "systems.yaml").write_text(
        "systems:\n  ZZZ:\n    system_type: SAP\n    display_name: Test ZZZ\n"
        "    environment: sandbox\n    password: leaked  # pragma: allowlist secret\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text("llm:\n  answer_model: from-temp-dir\n", encoding="utf-8")
    monkeypatch.setenv("DUMMYAI_CONFIG_DIR", str(tmp_path))
    reset_config_caches()

    systems = get_systems()
    assert list(systems) == ["ZZZ"]
    assert systems["ZZZ"].display_name == "Test ZZZ"
    assert not hasattr(systems["ZZZ"], "password")
    # config.yaml in the override dir feeds Settings too.
    assert Settings().llm.answer_model == "from-temp-dir"


def test_missing_config_dir_still_boots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cold start with no committed YAML must fall back to defaults, never raise."""
    monkeypatch.setenv("DUMMYAI_CONFIG_DIR", str(tmp_path))  # empty dir — no yaml files
    reset_config_caches()

    s = Settings()
    assert s.llm.answer_model == "gpt-4o"  # model default, not from YAML
    assert s.environment == "development"
    assert s.cors.allow_origins == ["http://localhost:5173"]

    assert get_systems() == {}
    assert get_metric_catalog() == {}
    assert get_metric_profiles() == {}
    assert get_tools_config().for_tool("anything") == {}
    assert validate_config() == []


def test_precedence_env_over_dotenv_over_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolution order must be: process env > .env > config.yaml (the dotenv middle layer)."""
    (tmp_path / "config.yaml").write_text("llm:\n  answer_model: from-yaml\n", encoding="utf-8")
    env_file = tmp_path / ".env.test"
    env_file.write_text("LLM__ANSWER_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("DUMMYAI_CONFIG_DIR", str(tmp_path))
    # Re-enable the dotenv layer (the autouse fixture disabled it), pointed at our temp file.
    monkeypatch.setitem(Settings.model_config, "env_file", str(env_file))
    reset_config_caches()

    # .env beats config.yaml
    assert Settings().llm.answer_model == "from-dotenv"

    # process env beats .env
    monkeypatch.setenv("LLM__ANSWER_MODEL", "from-process-env")
    reset_config_caches()
    assert Settings().llm.answer_model == "from-process-env"
