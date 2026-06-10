# SQL migrations

Numbered files applied in order:

- `0001_description.sql`, `0002_...sql`, etc.

**Phase 1** (vendor payload validation) is **complete**. **Phase 2** adds the first migration aligned with [../soma-planned-schema.sql](../soma-planned-schema.sql). Until `0001_*.sql` lands, the planned DDL lives only in that file for review and Bruno/Supabase manual experiments if needed.
