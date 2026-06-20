# Scripts

One-off tooling run from the **repository root** with the project venv active.

| Script | Purpose |
|--------|---------|
| [`smoke_hevy.py`](smoke_hevy.py) | Hevy: live fetch, raw disk, page-1 DB upsert, **full historical backfill** |
| [`smoke_apple_health.py`](smoke_apple_health.py) | Apple Health JSON → normalize → raw disk / DB upsert |
| [`smoke_caldav.py`](smoke_caldav.py) | CalDAV: list calendars, fetch, DB upsert (same path as Lambda) |
| [`smoke_strava.py`](smoke_strava.py) | Strava (paused — fixtures / live when token available) |

See [`.env.example`](../.env.example), [`docs/plans/local-dev-and-tooling.md`](../docs/plans/local-dev-and-tooling.md), and [`docs/plans/staging-validation-checklist.md`](../docs/plans/staging-validation-checklist.md) (operator soak + **Hevy backfill confirm/run**).
