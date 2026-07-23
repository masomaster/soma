-- Cycling power summaries (FIT ingest) + inferred FTP on the wide health layer.

ALTER TABLE public.cardio_events
    ADD COLUMN IF NOT EXISTS avg_watts FLOAT,
    ADD COLUMN IF NOT EXISTS max_watts FLOAT,
    ADD COLUMN IF NOT EXISTS normalized_power FLOAT,
    ADD COLUMN IF NOT EXISTS work_kj FLOAT,
    ADD COLUMN IF NOT EXISTS device_watts BOOLEAN,
    ADD COLUMN IF NOT EXISTS power_mmp_json JSONB;

COMMENT ON COLUMN public.cardio_events.avg_watts IS
    'Mean power (watts) over the session when a power meter stream is present.';
COMMENT ON COLUMN public.cardio_events.max_watts IS
    'Peak instantaneous power (watts) in the session.';
COMMENT ON COLUMN public.cardio_events.normalized_power IS
    'Coggan Normalized Power (watts) derived from the power stream at ingest.';
COMMENT ON COLUMN public.cardio_events.work_kj IS
    'Total mechanical work in kilojoules (sum of watts × sample_dt).';
COMMENT ON COLUMN public.cardio_events.device_watts IS
    'True when watts came from a power meter (not estimated).';
COMMENT ON COLUMN public.cardio_events.power_mmp_json IS
    'Per-session mean-maximal power curve: JSON object mapping duration_seconds (as text keys) to watts.';

ALTER TABLE public.daily_health_metrics
    ADD COLUMN IF NOT EXISTS ftp_watts FLOAT,
    ADD COLUMN IF NOT EXISTS ftp_method TEXT,
    ADD COLUMN IF NOT EXISTS ftp_confidence FLOAT;

COMMENT ON COLUMN public.daily_health_metrics.ftp_watts IS
    'Estimated functional threshold power (watts) from recent ride MMP (prefers 60/30-min anchors, else scaled CP / outdoor Coggan).';
COMMENT ON COLUMN public.daily_health_metrics.ftp_method IS
    'Estimator used: mmp_60 | mmp_30 | critical_power | coggan_20min | insufficient_data.';
COMMENT ON COLUMN public.daily_health_metrics.ftp_confidence IS
    'Heuristic confidence in [0, 1] for the FTP estimate (not a statistical CI).';
