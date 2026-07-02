"""ResultNormalizer: response parsing, summary stats, trend detection, series merge."""

from __future__ import annotations

from src.tools.prometheus_advanced_query.normalizer import ResultNormalizer
from src.tools.prometheus_advanced_query.schemas import TrendDirection

from ._helpers import matrix, matrix_multi, ramp

_NORM = ResultNormalizer()


def test_normalize_matrix_computes_summary() -> None:
    resp = matrix("sap_cpu", ramp([10, 20, 30, 40, 50]))
    results = _NORM.normalize_response(resp)
    assert len(results) == 1
    summary = results[0].summary
    assert summary.min == 10.0
    assert summary.max == 50.0
    assert summary.avg == 30.0
    assert summary.data_points == 5
    assert len(results[0].series) == 5


def test_metric_name_override_applied() -> None:
    resp = matrix("raw_name", ramp([1, 2, 3]))
    results = _NORM.normalize_response(resp, metric_name="logical_name")
    assert results[0].metric == "logical_name"


def test_increasing_series_trends_up() -> None:
    resp = matrix("m", ramp([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]))
    assert _NORM.normalize_response(resp)[0].summary.trend == TrendDirection.UP


def test_decreasing_series_trends_down() -> None:
    resp = matrix("m", ramp([10, 9, 8, 7, 6, 5, 4, 3, 2, 1]))
    assert _NORM.normalize_response(resp)[0].summary.trend == TrendDirection.DOWN


def test_flat_series_trends_flat() -> None:
    resp = matrix("m", ramp([5, 5, 5, 5, 5]))
    assert _NORM.normalize_response(resp)[0].summary.trend == TrendDirection.FLAT


def test_nan_replaced_in_series_and_excluded_from_stats() -> None:
    # A "NaN" sample becomes 0.0 in the series but is not counted in min/avg/data_points.
    resp = matrix("m", [[1000, "10"], [1060, "NaN"], [1120, "20"]])
    result = _NORM.normalize_response(resp)[0]
    assert result.summary.data_points == 2  # NaN excluded from valid values
    assert result.summary.avg == 15.0
    nan_point = result.series[1]
    assert nan_point.value == 0.0  # coerced for JSON safety


def test_inf_coerced_and_output_is_valid_json() -> None:
    # +Inf/-Inf (routine for Prometheus div-by-zero / empty-bucket quantiles) must NOT reach the
    # browser-visible result: coerced to 0.0 in the series and excluded from summary stats, so the
    # serialized dict is valid JSON (no 'Infinity' token).
    import json

    resp = matrix("m", [[1000, "10"], [1060, "+Inf"], [1120, "-Inf"], [1180, "30"]])
    result = _NORM.normalize_response(resp)[0]
    assert result.series[1].value == 0.0
    assert result.series[2].value == 0.0
    assert result.summary.data_points == 2  # only the two finite samples
    assert result.summary.max == 30.0
    # The whole dict round-trips through strict JSON (allow_nan=False rejects Infinity/NaN).
    dumped = json.dumps(result.model_dump(mode="json"), allow_nan=False)
    assert "Infinity" not in dumped


def test_percentile_linear_interpolation() -> None:
    p95 = _NORM._calculate_percentile([10, 20, 30, 40, 50], 95)
    assert p95 == 48.0  # 0.95*(5-1)=3.8 -> 40 + 0.8*(50-40)


def test_failed_response_normalizes_to_empty() -> None:
    from src.clients.prometheus import PrometheusResponse

    assert _NORM.normalize_response(PrometheusResponse(success=False)) == []


def test_merge_averages_finite_samples_per_timestamp() -> None:
    resp = matrix_multi(
        "m",
        [
            ({"monitoring_context": "app01"}, ramp([10, 20, 30])),
            ({"monitoring_context": "app02"}, ramp([30, 40, 50])),
        ],
    )
    series_list = _NORM.parse_series(resp, "m")
    assert len(series_list) == 2
    merged = _NORM.normalize_metric_data(_NORM.merge_metric_data(series_list, "m"))
    # Averaged per aligned timestamp: (10+30)/2, (20+40)/2, (30+50)/2
    assert [p.value for p in merged.series] == [20.0, 30.0, 40.0]
    assert merged.labels == {"_merged": "true", "_series_count": "2"}


def test_merge_skips_nan_gap_instead_of_counting_it_as_zero() -> None:
    # A missing scrape (NaN) on one app server must not be averaged in as a real 0.0: the merged
    # value at that timestamp is the mean of the FINITE samples only (here, just server B's).
    resp = matrix_multi(
        "m",
        [
            ({"monitoring_context": "app01"}, [[1000, "10"], [1060, "NaN"], [1120, "30"]]),
            ({"monitoring_context": "app02"}, [[1000, "30"], [1060, "40"], [1120, "50"]]),
        ],
    )
    merged = _NORM.merge_metric_data(_NORM.parse_series(resp, "m"), "m")
    values = [v for _, v in merged.values]
    # t0: (10+30)/2=20 ; t1: only 40 valid -> 40 (NOT (0+40)/2=20) ; t2: (30+50)/2=40
    assert values == [20.0, 40.0, 40.0]


def test_merge_drops_timestamp_missing_from_every_series() -> None:
    # A timestamp where all series are NaN is dropped, not fabricated as 0.0.
    resp = matrix_multi(
        "m",
        [
            ({"monitoring_context": "app01"}, [[1000, "10"], [1060, "NaN"]]),
            ({"monitoring_context": "app02"}, [[1000, "30"], [1060, "NaN"]]),
        ],
    )
    merged = _NORM.merge_metric_data(_NORM.parse_series(resp, "m"), "m")
    assert [ts for ts, _ in merged.values] == [1000]
