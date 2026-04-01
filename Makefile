.PHONY: help start update logs validate-fast install-hooks

VENV_DIR ?= odoo-venv
PYTHON ?= $(VENV_DIR)/bin/python
ODOO_BIN ?= odoo/odoo-bin
CONF_FILE ?= odoo.conf
MODULE ?= automotive_parts
LOG_FILE ?= odoo.log

help:
	@echo "Targets:"
	@echo "  make start   - run Odoo with $(CONF_FILE)"
	@echo "  make update  - update module $(MODULE)"
	@echo "  make logs    - tail $(LOG_FILE)"
	@echo "  make validate-fast - run local pre-commit checks"
	@echo "  make install-hooks - enable repo git hooks"

start:
	$(PYTHON) $(ODOO_BIN) -c $(CONF_FILE)

update:
	$(PYTHON) $(ODOO_BIN) -c $(CONF_FILE) -u $(MODULE)

logs:
	tail -f $(LOG_FILE)

validate-fast:
	bash scripts/pre_commit_validate.sh

install-hooks:
	bash scripts/install_git_hooks.sh
