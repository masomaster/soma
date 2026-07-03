"""Phase 6 rules-engine tests (thresholds + evaluation)."""

from __future__ import annotations

from pipeline import rules as R


def test_load_thresholds_overlays_ssm_and_ignores_bad_values():
    def get_parameters(prefix: str) -> dict[str, str]:
        assert prefix == "/soma/u1/rules/"
        return {
            f"{prefix}max_sleep_debt_7d": "3.5",  # valid override
            f"{prefix}unknown_knob": "1",  # ignored (not a known threshold)
            f"{prefix}min_readiness_score": "not-a-number",  # kept at default
        }

    th = R.load_thresholds(user_id="u1", get_parameters=get_parameters)
    assert th["max_sleep_debt_7d"] == 3.5
    assert "unknown_knob" not in th
    assert th["min_readiness_score"] == R.DEFAULT_THRESHOLDS["min_readiness_score"]


def test_load_thresholds_defaults_without_getter():
    assert R.load_thresholds(user_id="u1") == R.DEFAULT_THRESHOLDS


def test_evaluate_emits_expected_flags_worst_first():
    features = {
        "sleep_debt_7d": 7.0,
        "hrv_suppressed_days": 3,
        "acute_chronic_ratio": 2.0,
        "overall_readiness_score": 44.0,
        "recovery_sleep_days_7d": 7,
        "recovery_hrv_days_7d": 7,
    }
    flags = R.evaluate(features=features, daily_metrics={"sleep_hours": 5.0})
    codes = [f.code for f in flags]
    assert set(codes) == {
        "LOW_SLEEP",
        "HIGH_SLEEP_DEBT",
        "LOW_HRV",
        "HIGH_TRAINING_LOAD",
        "LOW_READINESS",
    }
    # Worst-first: the two alerts precede the warnings.
    assert {flags[0].severity, flags[1].severity} == {"alert"}
    assert all(f.severity == "warning" for f in flags[2:])


def test_evaluate_quiet_when_all_nominal():
    features = {
        "sleep_debt_7d": 1.0,
        "hrv_suppressed_days": 0,
        "acute_chronic_ratio": 1.0,
        "overall_readiness_score": 90.0,
        "recovery_sleep_days_7d": 7,
        "recovery_hrv_days_7d": 7,
    }
    assert R.evaluate(features=features, daily_metrics={"sleep_hours": 8.0}) == []


def test_evaluate_sparse_recovery_emits_info_flag():
    features = {
        "recovery_sleep_days_7d": 0,
        "recovery_hrv_days_7d": 0,
        "overall_readiness_score": None,
    }
    flags = R.evaluate(features=features, daily_metrics={})
    assert len(flags) == 1
    assert flags[0].code == "SPARSE_RECOVERY_DATA"
    assert flags[0].severity == "info"


def test_evaluate_skips_weekly_sleep_debt_when_sleep_coverage_zero():
    features = {
        "recovery_sleep_days_7d": 0,
        "recovery_hrv_days_7d": 4,
        "sleep_debt_7d": 99.0,
        "hrv_suppressed_days": 0,
        "acute_chronic_ratio": 1.0,
        "overall_readiness_score": 80.0,
    }
    codes = [f.code for f in R.evaluate(features=features, daily_metrics={})]
    assert "HIGH_SLEEP_DEBT" not in codes


def test_evaluate_skips_low_hrv_when_hrv_coverage_zero():
    features = {
        "recovery_sleep_days_7d": 5,
        "recovery_hrv_days_7d": 0,
        "sleep_debt_7d": 0.0,
        "hrv_suppressed_days": 5,
        "overall_readiness_score": 90.0,
    }
    codes = [f.code for f in R.evaluate(features=features, daily_metrics={})]
    assert "LOW_HRV" not in codes


def test_evaluate_legacy_rows_without_coverage_columns_keep_weekly_flags():
    """Pre-0002 rows may omit recovery_*; weekly debt/HRV flags still apply."""
    features = {
        "sleep_debt_7d": 7.0,
        "hrv_suppressed_days": 3,
        "acute_chronic_ratio": 2.0,
        "overall_readiness_score": 44.0,
    }
    codes = [f.code for f in R.evaluate(features=features, daily_metrics={"sleep_hours": 5.0})]
    assert "HIGH_SLEEP_DEBT" in codes
    assert "LOW_HRV" in codes
