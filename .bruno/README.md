# Bruno API collections (Soma)

Use [Bruno](https://www.usebruno.com/) to exercise **vendor APIs** and, later, **Supabase REST** — without committing secrets.

## Layout (suggested)

This repo includes `bruno.json` at `.bruno/bruno.json`, **Hevy** (`hevy/list-workouts.bru`), and **Strava** (`strava/list-athlete-activities.bru`). Open `.bruno` as a collection in Bruno and set `HEVY_API_KEY` / `STRAVA_ACCESS_TOKEN` in a secret / environment (never commit).

Create additional folders under `.bruno/` as you go, for example:

```text
.bruno/
  README.md                 ← this file
  bruno.json                ← collection root
  environments/
    soma.example.bru        ← committed template (copy to soma.bru locally; soma.bru is gitignored)
  hevy/
    folder.bru
    list-workouts.bru
  strava/
    folder.bru
    list-athlete-activities.bru
  supabase-rest/
    folder.bru
    get-strength-events.bru   ← after table exists; uses RLS + user JWT
```

Adjust names to match how you like to organize repos (single monorepo collection vs multiple collections).

## Secrets

- **Tracked:** `environments/soma.example.bru` — placeholders only; safe to commit.
- **Ignored:** `environments/soma.bru` (your real keys / tokens), `environments/*.local.bru`, and `.bruno/.env`. Copy the example: duplicate `soma.example.bru` → `soma.bru` in the same folder, replace the placeholder, pick the **soma** environment in Bruno.
- Never commit API keys, OAuth refresh tokens, or Supabase **service_role** keys.

## Supabase REST from Bruno

Example variables:

- `SUPABASE_URL` → `https://<project-ref>.supabase.co`
- `SUPABASE_ANON_KEY` → anon key (for RLS tests with a **user** JWT)
- `SUPABASE_USER_JWT` → short-lived access token from a test user (Auth → sign-in)

Request:

- `GET {{SUPABASE_URL}}/rest/v1/strength_events?select=*&limit=5`
- Headers: `apikey: {{SUPABASE_ANON_KEY}}`, `Authorization: Bearer {{SUPABASE_USER_JWT}}`

For **admin / ETL** behavior (bypass RLS), use the service role **only** on a trusted machine and prefer **not** to store that key in shared Bruno collections.

## Planning reference

See [docs/plans/local-dev-and-tooling.md](../docs/plans/local-dev-and-tooling.md) for the full non-Docker local workflow and how REST maps to the pipeline.
