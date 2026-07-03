# Default: `make` runs tests. Use `make install` once (or after Python upgrade).
.PHONY: install test compile cdk-synth dashboard dashboard-live guidelines-sync

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
