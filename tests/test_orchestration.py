"""Phase 5 orchestration tests: ordered daily pipeline with injected IO."""

from __future__ import annotations

from datetime import date, timedelta

from pipeline.briefing import Briefing
from pipeline.delivery import deliver_briefing
from pipeline.orchestration import DailyPipelineIO, run_daily_pipeline
from pipeline.settings import Environment

RUN = date(2024, 6, 8)


def _window_metrics():
    return [
        {"metric_date": RUN - timedelta(days=i), "sleep_hours": 5.0, "hrv_rmssd": 50.0}
        for i in range(1, 7)
    ]


def _io(persisted: dict, *, llm=None) -> DailyPipelineIO:
    return DailyPipelineIO(
        llm=llm or (lambda system, user: "Rest up — sleep debt is high."),
        load_biometrics_today=lambda u, d: [
            {"metric": "sleep_hours", "value": 5.0},
            {"metric": "hrv_rmssd", "value": 50.0},
        ],
        load_daily_metrics_window=lambda u, d: _window_metrics(),
        load_strength_events=lambda u, d: [
            {"event_date": RUN, "set_type": "working", "reps": 5, "weight_lbs": 100}
        ],
        load_cardio_events=lambda u, d: [{"event_date": RUN, "duration_min": 30, "session_rpe": None}],
        persist_daily_metrics=lambda row: persisted.setdefault("metrics", []).append(row),
        persist_features=lambda row: persisted.setdefault("features", []).append(row),
        persist_briefing=lambda row: persisted.setdefault("briefings", []).append(row),
        deliver=lambda b: deliver_briefing(b, env=Environment.LOCAL, stream=persisted["out"]),
    )


def test_full_pipeline_runs_steps_in_order_and_persists():
    import io as _io_mod

    persisted: dict = {"out": _io_mod.StringIO()}
    result = run_daily_pipeline(user_id="u1", run_date=RUN, io=_io(persisted))

    assert result.ok
    assert [s.name for s in result.steps] == [
        "rollup_metrics",
        "compute_features",
        "evaluate_rules",
        "generate_briefing",
        "deliver",
    ]
    assert result.daily_metrics["sleep_hours"] == 5.0
    assert result.features["strength_sessions_7d"] == 1
    # Sleep debt over the week (target 8h, 7 days at 5h) should flag.
    assert any(f.code == "HIGH_SLEEP_DEBT" for f in result.flags)
    assert isinstance(result.briefing, Briefing)
    assert persisted["briefings"][0]["coaching_note"].startswith("Rest up")
    assert result.delivery["channel"] == "stdout"


def test_pipeline_stops_cleanly_when_llm_fails():
    import io as _io_mod

    def boom(system: str, user: str) -> str:
        raise RuntimeError("anthropic 503")

    persisted: dict = {"out": _io_mod.StringIO()}
    result = run_daily_pipeline(user_id="u1", run_date=RUN, io=_io(persisted, llm=boom))

    assert not result.ok
    failed = [s for s in result.steps if not s.ok]
    assert len(failed) == 1 and failed[0].name == "generate_briefing"
    assert "anthropic 503" in failed[0].detail
    # Delivery must not run after a failed briefing.
    assert all(s.name != "deliver" for s in result.steps)
    assert result.delivery is None
    # Earlier steps still succeeded and persisted.
    assert "features" in persisted


if __name__ == "__main__":
    import sys

    import pytest

    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
