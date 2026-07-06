-- Training phases: multi-week blocks (building, deload, fat loss, running, etc.)

CREATE TABLE public.training_phases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    phase_type TEXT NOT NULL,
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    notes TEXT,
    target_notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (end_date >= start_date)
);

ALTER TABLE public.training_phases ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON public.training_phases
    FOR ALL USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE INDEX idx_training_phases_user_dates
    ON public.training_phases (user_id, start_date, end_date);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.training_phases TO authenticated;
GRANT ALL ON TABLE public.training_phases TO service_role;
