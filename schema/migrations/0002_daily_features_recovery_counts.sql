-- Recovery observation coverage for the 7-day acute window (distinct calendar
-- days with at least one non-null sleep_hours / hrv_rmssd in daily_health_metrics).
-- Used with sleep_debt_7d / readiness so sparse pipelines do not imply "perfect recovery."

ALTER TABLE public.daily_features
    ADD COLUMN IF NOT EXISTS recovery_sleep_days_7d INT,
    ADD COLUMN IF NOT EXISTS recovery_hrv_days_7d INT;

COMMENT ON COLUMN public.daily_features.strength_tonnage_7d IS
    'US short tons in the 7d window: sum(reps * weight_lbs) / 2000';
