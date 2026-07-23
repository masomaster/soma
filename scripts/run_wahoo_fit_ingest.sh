#!/usr/bin/env bash
# Recurring Wahoo BOLT → Dropbox FIT ingest for Soma.
# Loads repo .env; requires SOMA_USER_ID, SOMA_WAHOO_FIT_DIR, and
# SOMA_DATABASE_URL or DATABASE_URL (same as pipeline.fit_ingest).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

: "${SOMA_USER_ID:?Set SOMA_USER_ID in .env}"
if [[ -z "${SOMA_DATABASE_URL:-}" && -z "${DATABASE_URL:-}" ]]; then
  echo "Set SOMA_DATABASE_URL or DATABASE_URL in .env" >&2
  exit 1
fi
: "${SOMA_WAHOO_FIT_DIR:?Set SOMA_WAHOO_FIT_DIR to the Dropbox folder with Wahoo .fit files}"

FIT_DIR="$(eval echo "$SOMA_WAHOO_FIT_DIR")"
if [[ ! -d "$FIT_DIR" ]]; then
  echo "SOMA_WAHOO_FIT_DIR is not a directory: $FIT_DIR" >&2
  exit 1
fi

PY="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing $PY — run: make install" >&2
  exit 1
fi

LOG_DIR="${SOMA_WAHOO_FIT_LOG_DIR:-$REPO_ROOT/tmp/logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/wahoo-fit-ingest.log"

{
  echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) wahoo_fit ingest dir=$FIT_DIR ===="
  "$PY" -m pipeline.fit_ingest \
    --user-id "$SOMA_USER_ID" \
    --source wahoo_fit \
    --dir "$FIT_DIR" \
    --estimate-ftp
  echo "==== done ===="
} >>"$LOG_FILE" 2>&1
