-- Modality-split "training load" (v0) + unified effort heuristics / optional Foster RPE (v1 attempt).

ALTER TABLE public.cardio_events
    ADD COLUMN IF NOT EXISTS session_rpe FLOAT;

COMMENT ON COLUMN public.cardio_events.session_rpe IS
    'Optional post-session RPE (e.g. CR10 1–10). Foster load AU = duration_min * session_rpe when both are set.';

ALTER TABLE public.daily_features
    ADD COLUMN IF NOT EXISTS training_load_cardio_minutes_7d FLOAT,
    ADD COLUMN IF NOT EXISTS training_load_cardio_minutes_28d FLOAT,
    ADD COLUMN IF NOT EXISTS training_load_strength_short_tons_7d FLOAT,
    ADD COLUMN IF NOT EXISTS training_load_strength_short_tons_28d FLOAT,
    ADD COLUMN IF NOT EXISTS training_load_strength_hard_sets_28d INT,
    ADD COLUMN IF NOT EXISTS training_load_strength_sessions_28d INT,
    ADD COLUMN IF NOT EXISTS effort_unified_index_7d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_unified_index_28d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_foster_cardio_au_7d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_foster_strength_au_7d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_foster_au_7d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_foster_cardio_au_28d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_foster_strength_au_28d FLOAT,
    ADD COLUMN IF NOT EXISTS effort_foster_au_28d FLOAT;

COMMENT ON COLUMN public.daily_features.training_load_cardio_minutes_7d IS
    'External training load: sum of cardio duration_min in trailing 7d (mirrors cardio_minutes_7d).';
COMMENT ON COLUMN public.daily_features.training_load_strength_short_tons_7d IS
    'External training load: US short tons from working sets in 7d (mirrors strength_tonnage_7d).';
COMMENT ON COLUMN public.daily_features.effort_unified_index_7d IS
    'Heuristic arbitrary units: cardio minutes + strength short tons * k (see pipeline.features constants).';
COMMENT ON COLUMN public.daily_features.effort_foster_au_7d IS
    'Optional Foster internal load (AU): sum of cardio (duration*session_rpe) + strength proxy from set RPE when present.';
