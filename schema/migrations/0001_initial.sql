-- Soma initial schema (Supabase PostgreSQL).
-- Evolved from schema/soma-planned-schema.sql + Phase 1 Hevy (superset_id).
-- Requires: Supabase Auth (auth.users). gen_random_uuid() is available on Supabase Postgres.

-- -------------------------------------------------------
-- USERS (extends Supabase Auth)
-- -------------------------------------------------------
CREATE TABLE public.user_settings (
    user_id       UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    email         TEXT NOT NULL,
    timezone      TEXT DEFAULT 'America/Los_Angeles',
    briefing_time TIME DEFAULT '06:00:00',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- STRENGTH TRAINING (one row per set; Hevy superset_id from Phase 1 checklist)
-- -------------------------------------------------------
CREATE TABLE public.strength_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    event_date    DATE NOT NULL,
    exercise_name TEXT NOT NULL,
    muscle_group  TEXT,
    movement_type TEXT,
    superset_id   INTEGER,
    set_number    INT,
    reps          INT,
    weight_lbs    FLOAT,
    rpe           FLOAT,
    set_type      TEXT,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

-- -------------------------------------------------------
-- CARDIO / ENDURANCE
-- -------------------------------------------------------
CREATE TABLE public.cardio_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    event_date      DATE NOT NULL,
    activity_type   TEXT NOT NULL,
    duration_min    FLOAT,
    distance_miles  FLOAT,
    elevation_ft    FLOAT,
    avg_hr          INT,
    max_hr          INT,
    avg_pace_sec_mi INT,
    calories        INT,
    effort_zone     TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

-- -------------------------------------------------------
-- BIOMETRICS (EAV ingestion)
-- -------------------------------------------------------
CREATE TABLE public.biometrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    event_date  DATE NOT NULL,
    metric      TEXT NOT NULL,
    value       FLOAT NOT NULL,
    unit        TEXT,
    raw_s3_key  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source, event_date, metric)
);

-- -------------------------------------------------------
-- DAILY HEALTH METRICS (wide analysis layer)
-- -------------------------------------------------------
CREATE TABLE public.daily_health_metrics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    metric_date         DATE NOT NULL,
    hrv_rmssd           FLOAT,
    resting_hr          INT,
    spo2_pct            FLOAT,
    respiratory_rate    FLOAT,
    sleep_hours         FLOAT,
    sleep_deep_hrs      FLOAT,
    sleep_rem_hrs       FLOAT,
    sleep_score         FLOAT,
    steps               INT,
    active_cal          INT,
    vo2_max             FLOAT,
    body_weight_lbs     FLOAT,
    body_fat_pct        FLOAT,
    muscle_mass_lbs     FLOAT,
    hrv_7d_avg          FLOAT,
    hrv_30d_avg         FLOAT,
    hrv_baseline_ratio  FLOAT,
    sleep_7d_avg        FLOAT,
    weight_30d_trend    FLOAT,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, metric_date)
);

-- -------------------------------------------------------
-- DAILY FEATURES
-- -------------------------------------------------------
CREATE TABLE public.daily_features (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    feature_date            DATE NOT NULL,
    cardio_sessions_7d      INT,
    cardio_minutes_7d       FLOAT,
    cardio_minutes_14d      FLOAT,
    cardio_trimp_7d         FLOAT,
    acute_chronic_ratio     FLOAT,
    strength_sessions_7d    INT,
    strength_hard_sets_7d   INT,
    strength_tonnage_7d     FLOAT,
    upper_body_sets_7d      INT,
    lower_body_sets_7d      INT,
    push_sets_7d            INT,
    pull_sets_7d            INT,
    sleep_debt_7d           FLOAT,
    hrv_suppressed_days     INT,
    overall_readiness_score FLOAT,
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, feature_date)
);

-- -------------------------------------------------------
-- INTERVENTIONS
-- -------------------------------------------------------
CREATE TABLE public.interventions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    event_date  DATE NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    is_ongoing  BOOLEAN DEFAULT TRUE,
    end_date    DATE,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- DAILY BRIEFINGS
-- -------------------------------------------------------
CREATE TABLE public.daily_briefings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    briefing_date   DATE NOT NULL,
    flags           TEXT[],
    recommendations JSONB,
    features_json   JSONB,
    anomalies       JSONB,
    coaching_note   TEXT NOT NULL,
    model_used      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, briefing_date)
);

-- -------------------------------------------------------
-- ANOMALY LOG
-- -------------------------------------------------------
CREATE TABLE public.anomaly_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    detected_date DATE NOT NULL,
    metric        TEXT,
    anomaly_type  TEXT NOT NULL,
    description   TEXT NOT NULL,
    severity      TEXT,
    context_json  JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- ROW-LEVEL SECURITY
-- -------------------------------------------------------
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.strength_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cardio_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.biometrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_health_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_features ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.interventions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_briefings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.anomaly_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON public.user_settings FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.strength_events FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.cardio_events FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.biometrics FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.daily_health_metrics FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.daily_features FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.interventions FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.daily_briefings FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.anomaly_events FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

-- -------------------------------------------------------
-- INDEXES
-- -------------------------------------------------------
CREATE INDEX idx_strength_user_date ON public.strength_events (user_id, event_date DESC);

CREATE INDEX idx_strength_exercise ON public.strength_events (user_id, exercise_name, event_date DESC);

CREATE INDEX idx_strength_muscle ON public.strength_events (user_id, muscle_group, event_date DESC);

CREATE INDEX idx_cardio_user_date ON public.cardio_events (user_id, event_date DESC);

CREATE INDEX idx_cardio_type_date ON public.cardio_events (user_id, activity_type, event_date DESC);

CREATE INDEX idx_biometrics_metric ON public.biometrics (user_id, metric, event_date DESC);

CREATE INDEX idx_daily_metrics_date ON public.daily_health_metrics (user_id, metric_date DESC);

CREATE INDEX idx_daily_features_date ON public.daily_features (user_id, feature_date DESC);

CREATE INDEX idx_interventions_date ON public.interventions (user_id, event_date DESC);

CREATE INDEX idx_briefings_user_date ON public.daily_briefings (user_id, briefing_date DESC);

CREATE INDEX idx_anomalies_user_date ON public.anomaly_events (user_id, detected_date DESC);

-- -------------------------------------------------------
-- PostgREST: authenticated JWT users + service role (bypasses RLS)
-- -------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_settings TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.strength_events TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.cardio_events TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.biometrics TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.daily_health_metrics TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.daily_features TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.interventions TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.daily_briefings TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.anomaly_events TO authenticated;

GRANT ALL ON TABLE public.user_settings TO service_role;

GRANT ALL ON TABLE public.strength_events TO service_role;

GRANT ALL ON TABLE public.cardio_events TO service_role;

GRANT ALL ON TABLE public.biometrics TO service_role;

GRANT ALL ON TABLE public.daily_health_metrics TO service_role;

GRANT ALL ON TABLE public.daily_features TO service_role;

GRANT ALL ON TABLE public.interventions TO service_role;

GRANT ALL ON TABLE public.daily_briefings TO service_role;

GRANT ALL ON TABLE public.anomaly_events TO service_role;
