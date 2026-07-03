-- Source-aware cardio dedup: capture the originating HealthKit app + workout start.
--
-- All Apple Health hub cardio lands as source = 'apple_health', so the same run can
-- arrive up to 3x (Nike Run Club, Strava mirror of NRC, and Fitbit/Google via Health
-- Sync). We now keep the per-workout HealthKit source app name and the full start
-- timestamp so dedup can match by start time (robust to Fitbit's inaccurate
-- duration/distance) and resolve duplicates by source priority
-- (see pipeline.source_priority.CARDIO_SOURCE_APP_PRIORITY).

ALTER TABLE public.cardio_events
    ADD COLUMN IF NOT EXISTS source_app TEXT,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;

COMMENT ON COLUMN public.cardio_events.source_app IS
    'Originating HealthKit source app name for apple_health rows (e.g. "Nike Run Club", '
    '"Strava", "Health Sync" for Fitbit/Google, or a device name like "…''s Apple Watch"). '
    'Drives cross-app dedup priority; NULL for pre-0006 rows until backfilled from raw S3.';

COMMENT ON COLUMN public.cardio_events.started_at IS
    'Full workout start timestamp (TIMESTAMPTZ). event_date is the calendar day of this. '
    'Used to match near-duplicate sessions by start-time proximity; NULL for pre-0006 rows.';

-- No new index: the dedup existing-row scan and dashboard breakdown both filter by
-- (user_id, event_date), already served by idx_cardio_user_date from 0001.
