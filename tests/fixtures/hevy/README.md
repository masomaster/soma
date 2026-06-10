# Hevy fixtures

Shape matches **`GET /v1/workouts`** from the live API and [Hevy API Swagger UI](https://api.hevyapp.com/docs/): `page`, `page_count`, `workouts`; workout `id`, `title`, `routine_id` (nullable), `description`, `start_time`, `end_time`, `created_at`, `updated_at`; `exercises[]` with `index`, `title`, `notes`, `exercise_template_id`, **`superset_id`** (nullable int), `sets[]` with `index`, `type` (`warmup` | `normal` | …), `weight_kg` (nullable float), `reps`, optional `rpe`, nullable `distance_meters` / `duration_seconds` / `custom_metric`.

**Phase 1:** complete — this file is the contract reference for Phase 2 migrations + Phase 3 adapter. Refresh fixtures only with **redacted** snippets if the API changes.
