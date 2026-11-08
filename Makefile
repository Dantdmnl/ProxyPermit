PYTHON ?= python3
.DEFAULT_GOAL := help
.PHONY: help test check plan install serve check-server smoke version version-check release archive

help: ## Supported commands
	@echo "ProxyPermit"
	@echo "  make test                       Isolated unit tests"
	@echo "  make plan                       Read-only installation plan"
	@echo "  make install                    Install or upgrade ProxyPermit (sudo)"
	@echo "  make serve                      Start the selected WSGI server"
	@echo "  make check-server               Show the server command"
	@echo "  make smoke BACKEND=granian      HTTP smoke test with isolated state"
	@echo "  make check                     Run the same checks as CI"
	@echo "  make version-check             Validate generated versions"
	@echo "  make release VERSION=2.0.0     Prepare a local release (no Git operations)"
	@echo "  make archive                   Build a source archive and checksum"

check:
	$(PYTHON) scripts/check.py

version:
	$(PYTHON) scripts/release.py sync

version-check:
	$(PYTHON) scripts/release.py check

release:
	$(PYTHON) scripts/release.py prepare $(VERSION)

archive:
	$(PYTHON) scripts/release.py archive

test:
	$(PYTHON) scripts/dev/test.py

plan:
	$(PYTHON) scripts/deploy/install.py --plan $(ARGS)

install:
	sudo $(PYTHON) scripts/deploy/install.py --apply $(ARGS)

serve:
	$(PYTHON) -m app.runtime.server

check-server:
	$(PYTHON) -m app.runtime.server --check

BACKEND ?= gunicorn
smoke:
	$(PYTHON) scripts/dev/check_server.py --backend $(BACKEND)
