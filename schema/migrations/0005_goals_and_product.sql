-- Slices A–D + Phase 9: structured goals, running log, daily snapshots,
-- schedule exceptions, provider sync health, coaching chat history.

-- -------------------------------------------------------
-- GOALS (structured weekly targets)
-- -------------------------------------------------------
CREATE TABLE public.goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    goal_type TEXT NOT NULL,
    target_min INT,
    target_max INT,
    target_label TEXT,
    period TEXT NOT NULL DEFAULT 'weekly',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from DATE,
    effective_until DATE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, goal_type)
);

-- -------------------------------------------------------
-- RUNNING SESSIONS (manual log + deduped API rows)
-- -------------------------------------------------------
CREATE TABLE public.running_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    session_date DATE NOT NULL,
    run_type TEXT NOT NULL,
    distance_km FLOAT,
    duration_min FLOAT,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source, source_id)
);

-- -------------------------------------------------------
-- WEEKLY ACTIVITY SUMMARY (pipeline-computed rollups)
-- -------------------------------------------------------
CREATE TABLE public.weekly_activity_summary (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    week_start DATE NOT NULL,
    strength_sessions INT NOT NULL DEFAULT 0,
    running_km FLOAT NOT NULL DEFAULT 0,
    cardio_minutes FLOAT NOT NULL DEFAULT 0,
    summary_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, week_start)
);

-- -------------------------------------------------------
-- DAILY GOAL SNAPSHOT (pre-computed for briefing + dashboard)
-- -------------------------------------------------------
CREATE TABLE public.daily_goal_snapshot (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    goals_status JSONB NOT NULL,
    mileage_check JSONB,
    todays_focus TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, snapshot_date)
);

-- -------------------------------------------------------
-- SCHEDULE EXCEPTIONS (Slice D — travel, skip intervals, etc.)
-- -------------------------------------------------------
CREATE TABLE public.schedule_exceptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    affected_goal_types TEXT[] NOT NULL,
    override_hint TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- PROVIDER CONNECTIONS (Phase 9 sync health)
-- -------------------------------------------------------
CREATE TABLE public.provider_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'disconnected',
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, provider)
);

-- -------------------------------------------------------
-- COACHING CHAT (Slice C — multi-turn history)
-- -------------------------------------------------------
CREATE TABLE public.coaching_chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- -------------------------------------------------------
-- ROW-LEVEL SECURITY
-- -------------------------------------------------------
ALTER TABLE public.goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.running_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.weekly_activity_summary ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_goal_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.schedule_exceptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.coaching_chat_messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON public.goals FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.running_sessions FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.weekly_activity_summary FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.daily_goal_snapshot FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.schedule_exceptions FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.provider_connections FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.coaching_chat_messages FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

-- -------------------------------------------------------
-- INDEXES
-- -------------------------------------------------------
CREATE INDEX idx_goals_user_active ON public.goals (user_id, is_active);

CREATE INDEX idx_running_sessions_user_date ON public.running_sessions (user_id, session_date DESC);

CREATE INDEX idx_running_sessions_type ON public.running_sessions (user_id, run_type, session_date DESC);

CREATE INDEX idx_weekly_summary_user_week ON public.weekly_activity_summary (user_id, week_start DESC);

CREATE INDEX idx_daily_goal_snapshot_user_date ON public.daily_goal_snapshot (user_id, snapshot_date DESC);

CREATE INDEX idx_schedule_exceptions_user_dates ON public.schedule_exceptions (user_id, start_date, end_date);

CREATE INDEX idx_provider_connections_user ON public.provider_connections (user_id, provider);

CREATE INDEX idx_coaching_chat_user_time ON public.coaching_chat_messages (user_id, created_at DESC);

-- -------------------------------------------------------
-- GRANTS (match 0001 pattern)
-- -------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.goals TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.running_sessions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.weekly_activity_summary TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.daily_goal_snapshot TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.schedule_exceptions TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.provider_connections TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.coaching_chat_messages TO authenticated;

GRANT ALL ON TABLE public.goals TO service_role;
GRANT ALL ON TABLE public.running_sessions TO service_role;
GRANT ALL ON TABLE public.weekly_activity_summary TO service_role;
GRANT ALL ON TABLE public.daily_goal_snapshot TO service_role;
GRANT ALL ON TABLE public.schedule_exceptions TO service_role;
GRANT ALL ON TABLE public.provider_connections TO service_role;
GRANT ALL ON TABLE public.coaching_chat_messages TO service_role;
