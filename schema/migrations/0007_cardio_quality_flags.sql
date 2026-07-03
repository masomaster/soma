-- Field-level data-quality tags for cardio_events.
--
-- A single corrupt field (classically a run's distance from a GPS dropout,
-- giving an impossible pace) must not discard the session. Adapters tag such
-- rows at ingest (see pipeline.cardio_quality) so the session still counts for
-- frequency/duration while the suspect distance is excluded from mileage/pace
-- aggregates and surfaced for the athlete to verify. NULL / empty means clean
-- (all pre-0007 rows until re-ingested or backfilled).

ALTER TABLE public.cardio_events
    ADD COLUMN IF NOT EXISTS quality_flags TEXT[];

COMMENT ON COLUMN public.cardio_events.quality_flags IS
    'Data-quality tokens for this row (e.g. "implausible_run_pace" when the '
    'recorded distance yields a physically impossible pace). NULL/empty = clean. '
    'Set at ingest by pipeline.cardio_quality.assess_cardio_quality.';
