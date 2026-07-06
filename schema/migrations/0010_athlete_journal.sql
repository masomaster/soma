-- Athlete journal: dated subjective notes (workout feel, supplements, etc.)
-- managed via coaching chat and surfaced in briefing / dashboard context.

CREATE TABLE public.athlete_journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    entry_date DATE NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.athlete_journal_entries ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_isolation ON public.athlete_journal_entries
    FOR ALL USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

CREATE INDEX idx_athlete_journal_user_date
    ON public.athlete_journal_entries (user_id, entry_date DESC, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.athlete_journal_entries TO authenticated;
GRANT ALL ON TABLE public.athlete_journal_entries TO service_role;
