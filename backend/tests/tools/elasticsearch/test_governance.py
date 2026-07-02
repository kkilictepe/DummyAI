"""Response-governance primitives: projection, byte-cap, fit-to-cap, coercion, field validation."""

from __future__ import annotations

import json

from src.tools.elasticsearch.shared.profiles import get_profile
from src.tools.elasticsearch.shared.response_governance import (
    MINIMAL_PROJECTION_BASELINE,
    apply_minimal_projection,
    build_response_meta,
    coerce_json_object_arg,
    enforce_byte_cap,
    fit_items_to_cap,
    validate_field_filters,
)


def test_apply_minimal_projection_unions_and_sorts() -> None:
    result = apply_minimal_projection(["host", "component"])
    assert set(MINIMAL_PROJECTION_BASELINE).issubset(set(result))
    assert "host" in result and "component" in result
    assert result == sorted(result)


def test_coerce_json_object_arg_dict_passthrough_and_empty() -> None:
    assert coerce_json_object_arg({"a": 1}, "must_match") == {"a": 1}
    assert coerce_json_object_arg(None, "must_match") == {}
    assert coerce_json_object_arg("", "must_match") == {}
    assert coerce_json_object_arg('{"host":"a"}', "must_match") == {"host": "a"}


def test_coerce_json_object_arg_errors_are_sentinels() -> None:
    bad = coerce_json_object_arg("{not json", "must_match")
    assert isinstance(bad, str) and bad.startswith("__parse_error__:")
    not_obj = coerce_json_object_arg("[1,2,3]", "must_match")
    assert isinstance(not_obj, str) and "must decode to an object" in not_obj
    wrong_type = coerce_json_object_arg(123, "must_match")
    assert isinstance(wrong_type, str) and "must be a JSON object string" in wrong_type


def test_enforce_byte_cap_truncates_at_boundary() -> None:
    payload = "x" * 1000
    out, truncated, reason = enforce_byte_cap(payload, cap=100)
    assert truncated is True
    assert reason is not None and "100" in reason
    assert len(out.encode("utf-8")) <= 100

    small, trunc2, reason2 = enforce_byte_cap("short", cap=100)
    assert (small, trunc2, reason2) == ("short", False, None)


def test_fit_items_to_cap_finds_longest_prefix() -> None:
    items = [{"i": i, "pad": "y" * 50} for i in range(100)]

    def build(slice_: list[object], truncated: bool, reason: str | None) -> str:
        return json.dumps({"items": slice_, "truncated": truncated, "reason": reason})

    result, fitted, truncated, _reason = fit_items_to_cap(items, build, cap=1000)
    assert truncated is True
    assert 0 < fitted < len(items)
    assert len(result.encode("utf-8")) <= 1000
    # The returned payload embeds exactly `fitted` items (bytes/meta stay consistent).
    assert len(json.loads(result)["items"]) == fitted


def test_fit_items_to_cap_no_truncation_when_small() -> None:
    items = [1, 2, 3]

    def build(slice_: list[object], truncated: bool, reason: str | None) -> str:
        return json.dumps({"items": slice_, "truncated": truncated})

    result, fitted, truncated, reason = fit_items_to_cap(items, build, cap=1_000_000)
    assert (fitted, truncated, reason) == (3, False, None)
    assert json.loads(result)["items"] == [1, 2, 3]


def test_validate_field_filters_accepts_known_rejects_unknown() -> None:
    sap = get_profile("SAP")
    assert validate_field_filters({"host": "a", "system_id": "KHP"}, sap) is None

    err = validate_field_filters({"nonsense_field": "x"}, sap)
    assert err is not None
    assert err["status"] == "invalid_request"
    assert "nonsense_field" in err["reason"]
    assert "Searchable fields" in err["suggestion"]


def test_build_response_meta_shape() -> None:
    meta = build_response_meta(5, 12.3, 100, truncated=False)
    assert set(meta) == {
        "hit_count",
        "query_time_ms",
        "token_estimate",
        "truncated",
        "truncated_reason",
    }
    assert meta["truncated_reason"] is None
