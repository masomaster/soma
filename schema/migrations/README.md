# SQL migrations

Numbered files applied in order:

- `0001_description.sql`, `0002_...sql`, etc.

**Phase 1** (vendor payload validation) is **complete**. **Phase 2** ships [`0001_initial.sql`](./0001_initial.sql) (RLS + grants). Apply it to **staging** first, then prod, and follow [../../docs/plans/db-access-patterns.md](../../docs/plans/db-access-patterns.md) for keys and RLS checks. Keep [../soma-planned-schema.sql](../soma-planned-schema.sql) aligned when the model changes.
