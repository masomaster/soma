-- Soma planned core schema (Supabase PostgreSQL target).
-- Source of truth for planning: docs/plans/project-overview.md
-- When migrations exist in schema/migrations/, treat those as authoritative for applied DB state
-- and keep this file aligned or replace it with generated docs from migrations.
--
-- Requires: Supabase Auth (auth.users). gen_random_uuid() is available in Supabase by default.

-- -------------------------------------------------------
-- USERS (extends Supabase Auth)
-- -------------------------------------------------------
CREATE TABLE user_settings (
    user_id       UUID PRIMARY KEY REFERENCES auth.users(id),
    email         TEXT NOT NULL,
    timezone      TEXT DEFAULT 'America/Los_Angeles',
    briefing_time TIME DEFAULT '06:00:00',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- STRENGTH TRAINING
-- One row per set. Works for Hevy, Strong, Fitbod, etc.
-- -------------------------------------------------------
CREATE TABLE strength_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id),
    source        TEXT NOT NULL,        -- 'hevy', 'strong', 'manual'
    source_id     TEXT,                 -- for dedup
    event_date    DATE NOT NULL,
    exercise_name TEXT NOT NULL,
    muscle_group  TEXT,                 -- 'push', 'pull', 'legs', 'core'
    movement_type TEXT,                 -- 'compound', 'isolation'
    superset_id   INTEGER,              -- Hevy: nullable int grouping supersets
    set_number    INT,
    reps          INT,
    weight_lbs    FLOAT,
    rpe           FLOAT,
    set_type      TEXT,                 -- 'working', 'warmup', 'dropset'
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

-- -------------------------------------------------------
-- CARDIO / ENDURANCE ACTIVITIES
-- One row per session. Works for Strava, NRC, Garmin, etc.
-- -------------------------------------------------------
CREATE TABLE cardio_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id),
    source          TEXT NOT NULL,
    source_id       TEXT,
    event_date      DATE NOT NULL,
    activity_type   TEXT NOT NULL,      -- 'run', 'ride', 'swim', 'hike', 'walk'
    duration_min    FLOAT,
    distance_miles  FLOAT,
    elevation_ft    FLOAT,
    avg_hr          INT,
    max_hr          INT,
    avg_pace_sec_mi INT,
    calories        INT,
    effort_zone     TEXT,               -- 'zone1'–'zone5'
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

-- -------------------------------------------------------
-- BIOMETRICS (EAV — flexible ingestion layer)
-- One row per metric per day per source.
-- Good for ingestion; use daily_health_metrics for analysis.
-- -------------------------------------------------------
CREATE TABLE biometrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES auth.users(id),
    source      TEXT NOT NULL,
    event_date  DATE NOT NULL,
    metric      TEXT NOT NULL,
    value       FLOAT NOT NULL,
    unit        TEXT,
    raw_s3_key  TEXT,              -- traceability back to raw file
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source, event_date, metric)
);

-- Canonical metric names (all ETL adapters must use these exact strings):
-- 'hrv_rmssd'        ms
-- 'resting_hr'       bpm
-- 'sleep_hours'      hours
-- 'sleep_deep_hrs'   hours
-- 'sleep_rem_hrs'    hours
-- 'sleep_score'      0-100
-- 'steps'            count
-- 'active_cal'       kcal
-- 'vo2_max'          ml/kg/min
-- 'body_weight_lbs'  lbs
-- 'body_fat_pct'     pct
-- 'muscle_mass_lbs'  lbs
-- 'spo2_pct'         pct
-- 'respiratory_rate' breaths/min

-- -------------------------------------------------------
-- DAILY HEALTH METRICS (wide table — analysis layer)
-- Derived from biometrics. One row per user per day.
-- -------------------------------------------------------
CREATE TABLE daily_health_metrics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES auth.users(id),
    metric_date         DATE NOT NULL,
    -- Recovery
    hrv_rmssd           FLOAT,
    resting_hr          INT,
    spo2_pct            FLOAT,
    respiratory_rate    FLOAT,
    -- Sleep
    sleep_hours         FLOAT,
    sleep_deep_hrs      FLOAT,
    sleep_rem_hrs       FLOAT,
    sleep_score         FLOAT,
    -- Activity
    steps               INT,
    active_cal          INT,
    vo2_max             FLOAT,
    -- Body composition
    body_weight_lbs     FLOAT,
    body_fat_pct        FLOAT,
    muscle_mass_lbs     FLOAT,
    -- Derived / computed
    hrv_7d_avg          FLOAT,
    hrv_30d_avg         FLOAT,
    hrv_baseline_ratio  FLOAT,
    sleep_7d_avg        FLOAT,
    weight_30d_trend    FLOAT,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, metric_date)
);

-- -------------------------------------------------------
-- DAILY FEATURES (computed training load + readiness)
-- -------------------------------------------------------
CREATE TABLE daily_features (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES auth.users(id),
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
-- INTERVENTIONS (life events that affect health data)
-- -------------------------------------------------------
CREATE TABLE interventions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id),
    event_date      DATE NOT NULL,
    category        TEXT NOT NULL,
    description     TEXT NOT NULL,
    is_ongoing      BOOLEAN DEFAULT TRUE,
    end_date        DATE,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- DAILY BRIEFINGS (persisted pipeline output)
-- -------------------------------------------------------
CREATE TABLE daily_briefings (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES auth.users(id),
    briefing_date  DATE NOT NULL,
    flags          TEXT[],
    recommendations JSONB,
    features_json  JSONB,
    anomalies      JSONB,
    coaching_note  TEXT NOT NULL,
    model_used     TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, briefing_date)
);

-- -------------------------------------------------------
-- ANOMALY LOG
-- -------------------------------------------------------
CREATE TABLE anomaly_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id),
    detected_date   DATE NOT NULL,
    metric          TEXT,
    anomaly_type    TEXT NOT NULL,
    description     TEXT NOT NULL,
    severity        TEXT,
    context_json    JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- ROW-LEVEL SECURITY
-- -------------------------------------------------------
ALTER TABLE strength_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE cardio_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE biometrics            ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_health_metrics  ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_features        ENABLE ROW LEVEL SECURITY;
ALTER TABLE interventions         ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_briefings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomaly_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings         ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON strength_events
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON cardio_events
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON biometrics
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON daily_health_metrics
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON daily_features
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON interventions
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON daily_briefings
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON anomaly_events
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
CREATE POLICY user_isolation ON user_settings
    USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- -------------------------------------------------------
-- INDEXES
-- -------------------------------------------------------
CREATE INDEX idx_strength_user_date       ON strength_events(user_id, event_date DESC);
CREATE INDEX idx_strength_exercise        ON strength_events(user_id, exercise_name, event_date DESC);
CREATE INDEX idx_strength_muscle          ON strength_events(user_id, muscle_group, event_date DESC);
CREATE INDEX idx_cardio_user_date         ON cardio_events(user_id, event_date DESC);
CREATE INDEX idx_cardio_type_date         ON cardio_events(user_id, activity_type, event_date DESC);
CREATE INDEX idx_biometrics_metric        ON biometrics(user_id, metric, event_date DESC);
CREATE INDEX idx_daily_metrics_date       ON daily_health_metrics(user_id, metric_date DESC);
CREATE INDEX idx_daily_features_date      ON daily_features(user_id, feature_date DESC);
CREATE INDEX idx_interventions_date       ON interventions(user_id, event_date DESC);
CREATE INDEX idx_briefings_user_date      ON daily_briefings(user_id, briefing_date DESC);
CREATE INDEX idx_anomalies_user_date      ON anomaly_events(user_id, detected_date DESC);
