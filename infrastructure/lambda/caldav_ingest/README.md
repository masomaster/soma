# CalDAV calendar ingest Lambda

EventBridge Scheduler → poll **iCloud CalDAV** → raw S3 → **`interventions`**
(`calendar_busy`).

## Secrets

CDK creates dedicated secrets (see `soma_cdk.runtime_secrets.RuntimeSecrets`):

| Secret | Format |
|--------|--------|
| `soma-caldav` | JSON: `CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD` |
| `soma-db` | plain Postgres URI |
| `soma-tenant` | plain Supabase `auth.users` UUID |

- **`CALDAV_PASSWORD`** is an [app-specific password](https://appleid.apple.com)
  (Sign-In and Security → App-Specific Passwords), **not** your Apple ID password.

Placeholder `update_me` is treated as unset — the Lambda will fail until you replace
these values. Use stack parameter **`SeedRuntimeSecrets=No`** on the staging stack
after filling secrets so deploys do not reset them.

Optional Lambda env **`CALDAV_CALENDAR_NAME`** (case-insensitive; substring allowed): when set,
only matching calendars are polled — e.g. **`Mason`** for your personal busy blocks.
**Do not** ingest partner/shared calendars (e.g. **`Caroline`**) unless you want those
events treated as blocking; their events often do not affect your training availability.

When unset, **all** calendars are searched (not recommended on shared iCloud accounts).

## Lambda layer note

The handler imports **`caldav`** (bundled in the shared pipeline Lambda layer via
`soma_cdk/pipeline_layer.py`). Redeploy after layer changes.

## Schedule

CDK creates EventBridge **Scheduler** `soma-{env}-caldav-ingest` at **08:00 UTC** on staging
(**disabled on prod** until Phase 11). Work calendars outside iCloud will **not** appear
in this poll — only events visible via the configured CalDAV account.
