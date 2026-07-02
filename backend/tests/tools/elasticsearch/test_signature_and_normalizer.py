"""SignatureExtractor determinism + LogNormalizer classification / severity / timestamp parsing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.tools.elasticsearch.shared.normalizer import LogNormalizer
from src.tools.elasticsearch.shared.profiles import GenericProfile, SapProfile
from src.tools.elasticsearch.shared.schemas import ErrorCategory
from src.tools.elasticsearch.shared.signature_extractor import SignatureExtractor


def test_signature_identical_messages_match() -> None:
    ext = SignatureExtractor(SapProfile())
    assert ext.generate_signature("Connection refused") == ext.generate_signature(
        "Connection refused"
    )


def test_signature_collapses_variable_numbers_and_timestamps() -> None:
    ext = SignatureExtractor(GenericProfile())
    # 6+ digit numbers -> <NUMBER>; the two messages share one signature.
    assert ext.generate_signature("job 123456 failed") == ext.generate_signature(
        "job 999999 failed"
    )
    # ISO timestamps -> <TIMESTAMP>.
    a = ext.generate_signature("started at 2026-06-01T10:00:00Z")
    b = ext.generate_signature("started at 2026-06-02T11:22:33Z")
    assert a == b


def test_signature_distinct_templates_differ() -> None:
    ext = SignatureExtractor(SapProfile())
    assert ext.generate_signature("RFC destination QAS failed") != ext.generate_signature(
        "Short dump RABAX occurred"
    )


def test_sap_variable_pattern_collapses_work_process() -> None:
    ext = SignatureExtractor(SapProfile())
    assert ext.generate_signature("error in WP 12") == ext.generate_signature("error in WP 7")


def test_normalizer_classifies_sap_memory_dump() -> None:
    norm = LogNormalizer(profile=SapProfile())
    mem = norm.normalize_log(
        {"msg_text": "TSV_TNEW_PAGE_ALLOC_FAILED during report run", "system_id": "KHP"},
        "d1",
        "idx",
    )
    assert mem.error_category == ErrorCategory.MEMORY

    dump = norm.normalize_log(
        {"msg_text": "ABAP short dump occurred (RABAX)", "system_id": "KHP"}, "d2", "idx"
    )
    assert dump.error_category == ErrorCategory.SAP_DUMP


def test_normalizer_rfc_classification() -> None:
    norm = LogNormalizer(profile=SapProfile())
    log = norm.normalize_log(
        {"msg_text": "RFC destination FOO COMMUNICATION_FAILURE", "system_id": "KHP"}, "d", "idx"
    )
    assert log.error_category == ErrorCategory.SAP_RFC


def test_normalizer_severity_nested_log_level_and_sap_override() -> None:
    norm = LogNormalizer(profile=SapProfile())
    nested = norm.normalize_log({"message": "x", "log": {"level": "error"}}, "d1", "idx")
    assert nested.severity == "ERROR"
    # SAP abort code 'A' -> CRITICAL via profile override.
    abort = norm.normalize_log({"message": "x", "severity": "A"}, "d2", "idx")
    assert abort.severity == "CRITICAL"


def test_normalizer_timestamp_is_utc_aware() -> None:
    norm = LogNormalizer(profile=SapProfile())
    log = norm.normalize_log(
        {"message": "x", "@timestamp": "2026-06-01T10:00:00Z", "system_id": "KHP"}, "d", "idx"
    )
    assert log.timestamp.tzinfo is not None
    assert log.timestamp.utcoffset() == UTC.utcoffset(None)


def test_normalizer_prefers_msg_text_over_message() -> None:
    norm = LogNormalizer(profile=GenericProfile())
    log = norm.normalize_log({"msg_text": "real line", "message": "fallback"}, "d", "idx")
    assert log.raw_message == "real line"


def test_normalizer_parses_slash_and_space_timestamps() -> None:
    # Regression: the fallback format table previously appended a literal 'Z' that these formats
    # have no directive for, silently dropping them to now(). They must parse to their real time.
    norm = LogNormalizer(profile=SapProfile())
    slash = norm.normalize_log(
        {"message": "x", "time": "2026/06/01 10:00:00", "system_id": "KHP"}, "d1", "idx"
    )
    assert slash.timestamp == datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    space = norm.normalize_log(
        {"message": "x", "time": "2026-06-01 09:30:00", "system_id": "KHP"}, "d2", "idx"
    )
    assert space.timestamp == datetime(2026, 6, 1, 9, 30, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("recv failed on socket", ErrorCategory.SAP_RFC),
        ("CM_PRODUCT_SPECIFIC_ERROR raised", ErrorCategory.SAP_RFC),
        ("STORAGE_PARAMETERS_WRONG detected", ErrorCategory.MEMORY),
    ],
)
def test_normalizer_classifies_without_a_gate_keyword(
    message: str, expected: ErrorCategory
) -> None:
    # These match a SAP category regex but contain no quick_keyword; tier-2 must still classify
    # them (the keyword gate was removed because it was narrower than the regex set).
    norm = LogNormalizer(profile=SapProfile())
    log = norm.normalize_log({"msg_text": message, "system_id": "KHP"}, "d", "idx")
    assert log.error_category == expected


def test_generic_concurrent_modification_is_lock() -> None:
    norm = LogNormalizer(profile=GenericProfile())
    log = norm.normalize_log({"msg_text": "concurrent modification detected"}, "d", "idx")
    assert log.error_category == ErrorCategory.LOCK


def test_normalizer_tier3_log_name_classification() -> None:
    # Message text matches no regex; classification falls through to the log_name map (tier 3).
    norm = LogNormalizer(profile=SapProfile())
    log = norm.normalize_log(
        {"msg_text": "routine entry", "log_name": "sap.application.shortdumps", "system_id": "KHP"},
        "d",
        "idx",
    )
    assert log.error_category == ErrorCategory.SAP_DUMP


def test_normalizer_tier4_function_type_classification() -> None:
    # No message/log_name signal; the lowercase system_function_type is upper-cased before lookup.
    norm = LogNormalizer(profile=SapProfile())
    log = norm.normalize_log(
        {"msg_text": "routine entry", "system_function_type": "rfc", "system_id": "KHP"},
        "d",
        "idx",
    )
    assert log.error_category == ErrorCategory.SAP_RFC
