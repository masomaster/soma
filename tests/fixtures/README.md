# Test fixtures

- **`hevy/`** — Redacted samples for `GET https://api.hevyapp.com/v1/workouts` (Phase **1** complete — live-validated); see `hevy/README.md` and [api.hevyapp.com/docs](https://api.hevyapp.com/docs/). Keep secrets and raw personal data out of git.
- **`strava/`** — Synthetic samples for `GET https://www.strava.com/api/v3/athlete/activities` (JSON array); see `strava/README.md` and [Strava API reference](https://developers.strava.com/docs/reference/).
- **`biometrics/`** — Redacted daily rollup rows use the **`metric`** key (maps to `biometrics.metric`); values are canonical names from `schema/soma-planned-schema.sql`.
