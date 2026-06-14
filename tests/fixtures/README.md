# Test fixtures

- **`hevy/`** — Redacted samples for `GET https://api.hevyapp.com/v1/workouts` (Phase **1** complete — live-validated); see `hevy/README.md` and [api.hevyapp.com/docs](https://api.hevyapp.com/docs/). Keep secrets and raw personal data out of git.
- **`strava/`** — Synthetic samples for `GET https://www.strava.com/api/v3/athlete/activities` (JSON array); see `strava/README.md` and [Strava API reference](https://developers.strava.com/docs/reference/).
- **`biometrics/`** — `health_export_daily_redacted.json`: Soma daily envelope. `health_auto_export_metrics_redacted.json`: HAE `data.metrics`. `health_auto_export_workouts_redacted.json`: HAE `data.workouts` (cardio). See [apple-health-export.md](../../docs/plans/apple-health-export.md).
