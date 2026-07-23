# Default: `make` runs tests. Use `make install` once (or after Python upgrade).
.PHONY: install test compile cdk-synth dashboard dashboard-live guidelines-sync guidelines-condense \
	wahoo-fit-ingest wahoo-fit-ingest-install wahoo-fit-ingest-uninstall

.DEFAULT_GOAL := test

PYTHON ?= python3.14
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
STREAMLIT := $(VENV)/bin/streamlit

# Guidelines corpus sync (local markdown -> S3). Override any of these as needed:
#   SOMA_GUIDELINES_LOCAL_DIR  local root that contains guidelines/{user_id}/*.md
#   SOMA_GUIDELINES_BUCKET     skip the CloudFormation lookup and target this bucket
#   GUIDELINES_STACK           CloudFormation stack that emits GuidelinesBucketName
#   SYNC_FLAGS                 extra aws s3 sync flags, e.g. SYNC_FLAGS=--dryrun
SOMA_GUIDELINES_LOCAL_DIR ?= tmp/soma_guidelines
GUIDELINES_STACK ?= SomaStagingStack
SYNC_FLAGS ?=

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

test:
	$(PY) -m pytest tests/ -q

compile:
	$(PY) -m compileall -q pipeline

cdk-synth:
	$(PIP) install -q -e ".[cdk]"
	cd $(CURDIR)/infrastructure && PATH="$(CURDIR)/$(VENV)/bin:$$PATH" npx --yes aws-cdk@2 synth --all

# Launch the Streamlit dashboard. Auto-creates .venv if missing and installs the
# dashboard extra, so a fresh clone just needs `make dashboard`.
# Default is fixture mode (no DB/secrets needed). Use `make dashboard-live` for live data.
dashboard:
	@test -x $(PY) || $(MAKE) install
	$(PIP) install -q -e ".[dashboard]"
	SOMA_DASHBOARD_FIXTURE=1 $(STREAMLIT) run dashboard/app.py

dashboard-live:
	@test -x $(PY) || $(MAKE) install
	$(PIP) install -q -e ".[dashboard]"
	SOMA_DASHBOARD_FIXTURE=0 $(STREAMLIT) run dashboard/app.py

# Print (or --llm draft) expert-principles condensation from
# tmp/guidelines-transcripts/. See scripts/guidelines-corpus.md.
TRANSCRIPTS_DIR ?= tmp/guidelines-transcripts
guidelines-condense:
	@test -x $(PY) || $(MAKE) install
	$(PY) scripts/condense_expert_principles.py --transcripts-dir "$(TRANSCRIPTS_DIR)" --print-prompt

# Upload the local guidelines corpus (my-goals.md / injury-history.md /
# expert-principles.md under guidelines/{user_id}/) to the deployed S3 bucket.
# The briefing Lambda reads it live — no redeploy needed. Requires valid AWS creds.
guidelines-sync:
	@bucket="$(SOMA_GUIDELINES_BUCKET)"; \
	if [ -z "$$bucket" ]; then \
		echo "Resolving guidelines bucket from stack $(GUIDELINES_STACK)…"; \
		bucket=$$(aws cloudformation describe-stacks --stack-name "$(GUIDELINES_STACK)" \
			--query "Stacks[0].Outputs[?contains(OutputKey,'GuidelinesBucketName')].OutputValue" \
			--output text); \
	fi; \
	if [ -z "$$bucket" ] || [ "$$bucket" = "None" ]; then \
		echo "ERROR: could not resolve guidelines bucket. Set SOMA_GUIDELINES_BUCKET or check stack $(GUIDELINES_STACK)."; \
		exit 1; \
	fi; \
	echo "Syncing $(SOMA_GUIDELINES_LOCAL_DIR)/guidelines/ -> s3://$$bucket/guidelines/"; \
	aws s3 sync "$(SOMA_GUIDELINES_LOCAL_DIR)" "s3://$$bucket" --exclude "*" --include "guidelines/*" $(SYNC_FLAGS)

# --- Wahoo Dropbox FIT ingest (optional Mac fallback; prefer Dropbox API Lambda) ---
# Requires .env: SOMA_USER_ID, SOMA_WAHOO_FIT_DIR, and SOMA_DATABASE_URL or DATABASE_URL
# Optional schedule in .env (or make/env override): SOMA_WAHOO_FIT_HOUR / SOMA_WAHOO_FIT_MINUTE
# Priority for install schedule: make/env override > repo .env > defaults 21:00
LAUNCH_AGENTS := $(HOME)/Library/LaunchAgents
WAHOO_PLIST_LABEL := com.soma.wahoo-fit-ingest
WAHOO_PLIST := $(LAUNCH_AGENTS)/$(WAHOO_PLIST_LABEL).plist

wahoo-fit-ingest:
	@test -x $(CURDIR)/scripts/run_wahoo_fit_ingest.sh || chmod +x $(CURDIR)/scripts/run_wahoo_fit_ingest.sh
	$(CURDIR)/scripts/run_wahoo_fit_ingest.sh

wahoo-fit-ingest-install:
	@test -x $(CURDIR)/scripts/run_wahoo_fit_ingest.sh || chmod +x $(CURDIR)/scripts/run_wahoo_fit_ingest.sh
	@mkdir -p "$(LAUNCH_AGENTS)" "$(CURDIR)/tmp/logs"
	@HOUR_OVERRIDE="$(SOMA_WAHOO_FIT_HOUR)"; \
	MINUTE_OVERRIDE="$(SOMA_WAHOO_FIT_MINUTE)"; \
	if [ -f "$(CURDIR)/.env" ]; then set -a; . "$(CURDIR)/.env"; set +a; fi; \
	if [ -n "$$HOUR_OVERRIDE" ]; then SOMA_WAHOO_FIT_HOUR="$$HOUR_OVERRIDE"; fi; \
	if [ -n "$$MINUTE_OVERRIDE" ]; then SOMA_WAHOO_FIT_MINUTE="$$MINUTE_OVERRIDE"; fi; \
	HOUR="$${SOMA_WAHOO_FIT_HOUR:-21}"; \
	MINUTE="$${SOMA_WAHOO_FIT_MINUTE:-0}"; \
	sed \
		-e 's|__SOMA_REPO__|$(CURDIR)|g' \
		-e "s|__SOMA_HOUR__|$$HOUR|g" \
		-e "s|__SOMA_MINUTE__|$$MINUTE|g" \
		"$(CURDIR)/ops/macos/com.soma.wahoo-fit-ingest.plist.in" > "$(WAHOO_PLIST)"; \
	launchctl bootout "gui/$$(id -u)/$(WAHOO_PLIST_LABEL)" 2>/dev/null || true; \
	launchctl bootstrap "gui/$$(id -u)" "$(WAHOO_PLIST)"; \
	launchctl enable "gui/$$(id -u)/$(WAHOO_PLIST_LABEL)" 2>/dev/null || true; \
	echo "Installed $(WAHOO_PLIST) — daily at $$HOUR:$$(printf '%02d' $$MINUTE) local"; \
	echo "Requires SOMA_WAHOO_FIT_DIR in .env. Test now: make wahoo-fit-ingest"; \
	echo "Logs: $(CURDIR)/tmp/logs/wahoo-fit-ingest.log"

wahoo-fit-ingest-uninstall:
	@launchctl bootout "gui/$$(id -u)/$(WAHOO_PLIST_LABEL)" 2>/dev/null || true
	@rm -f "$(WAHOO_PLIST)"
	@echo "Removed $(WAHOO_PLIST_LABEL)"
