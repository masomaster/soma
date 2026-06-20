-- Phase 8 Layer 1 + Layer 3: metric baselines and cross-metric patterns.

CREATE TABLE public.metric_baselines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    metric_date DATE NOT NULL,
    metric TEXT NOT NULL,
    window_days INT NOT NULL,
    mean_value FLOAT,
    stdev_value FLOAT,
    sample_n INT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, metric_date, metric, window_days)
);

CREATE TABLE public.metric_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    metric_a TEXT NOT NULL,
    metric_b TEXT NOT NULL,
    lag_days INT NOT NULL DEFAULT 0,
    correlation FLOAT,
    sample_n INT,
    status TEXT NOT NULL DEFAULT 'active',
    description TEXT,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    last_confirmed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, metric_a, metric_b, lag_days)
);

ALTER TABLE public.metric_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.metric_patterns ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON public.metric_baselines FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE POLICY user_isolation ON public.metric_patterns FOR ALL USING (user_id = auth.uid())
WITH
    CHECK (user_id = auth.uid());

CREATE INDEX idx_metric_baselines_user_date ON public.metric_baselines (user_id, metric_date DESC);

CREATE INDEX idx_metric_patterns_user_status ON public.metric_patterns (user_id, status, last_confirmed_at DESC);

-- Optional hardening: idempotent statistical anomaly upserts without delete-first.
CREATE UNIQUE INDEX IF NOT EXISTS idx_anomaly_statistical_unique ON public.anomaly_events (
    user_id,
    detected_date,
    metric
) WHERE anomaly_type = 'statistical' AND metric IS NOT NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.metric_baselines TO authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.metric_patterns TO authenticated;
