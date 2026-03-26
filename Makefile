.PHONY: help start update logs

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

start:
	$(PYTHON) $(ODOO_BIN) -c $(CONF_FILE)

update:
	$(PYTHON) $(ODOO_BIN) -c $(CONF_FILE) -u $(MODULE)

logs:
	tail -f $(LOG_FILE)

