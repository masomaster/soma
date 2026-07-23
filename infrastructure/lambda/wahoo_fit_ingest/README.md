"""EventBridge Scheduler cron → Dropbox API → raw S3 → ``cardio_events`` + FTP.

Schedule name: ``soma-wahoo-fit-ingest`` (default **08:30 UTC**). Fill Secrets
Manager ``soma-dropbox`` after deploy, then redeploy with ``SeedRuntimeSecrets=No``.
See ``docs/plans/cycling-power-ftp.md``.
"""
