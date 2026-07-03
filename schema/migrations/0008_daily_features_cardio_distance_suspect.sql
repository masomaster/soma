-- Persist the 7-day count of cardio sessions whose recorded distance is
-- physically implausible (see pipeline.cardio_quality / migration 0007).
--
-- compute_daily_features now emits cardio_distance_suspect_7d; the rules engine
-- turns a non-zero count into the info-level DATA_QUALITY_CARDIO_DISTANCE flag.
-- Nullable with no default: pre-0008 rows read NULL (treated as 0 downstream).

ALTER TABLE public.daily_features
    ADD COLUMN IF NOT EXISTS cardio_distance_suspect_7d INTEGER;

COMMENT ON COLUMN public.daily_features.cardio_distance_suspect_7d IS
    'Count of cardio sessions in the trailing 7 days whose recorded distance '
    'yields an implausible run pace (excluded from weekly mileage). '
    'Computed by pipeline.features.compute_daily_features.';
