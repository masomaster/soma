# Default: `make` runs tests. Use `make install` once (or after Python upgrade).
.PHONY: install test compile cdk-synth dashboard dashboard-live

.DEFAULT_GOAL := test

PYTHON ?= python3.14
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
STREAMLIT := $(VENV)/bin/streamlit

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
