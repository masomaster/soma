# Default: `make` runs tests. Use `make install` once (or after Python upgrade).
.PHONY: install test compile cdk-synth
.DEFAULT_GOAL := test

PYTHON ?= python3.14
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

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
	cd $(CURDIR)/infrastructure && PATH="$(CURDIR)/$(VENV)/bin:$$PATH" npx --yes aws-cdk@2 synth SomaStagingStack SomaProdStack
