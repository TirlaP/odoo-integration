---
name: odoo-module-workflow
description: Use when implementing or modifying Odoo custom addons. Covers the default workflow for models, manifests, views, security, data files, and validation in self-hosted Odoo projects.
---

# Odoo Module Workflow

Use this skill for normal Odoo addon work in this repository.

## Default scope

- Work inside `custom_addons/*` unless the task explicitly requires core Odoo changes.
- Treat standard Odoo apps as the baseline. Add custom business logic only where the requirement is not already satisfied by configuration or standard behavior.
- Prefer extending standard models over replacing standard workflows.

## Required implementation checklist

When adding or changing a feature in an addon:

1. Inspect:
   - `__manifest__.py`
   - `models/__init__.py`
   - relevant `models/*.py`
   - relevant `views/*.xml`
   - relevant `security/*`
2. If adding a new model:
   - define the model in `models/*.py`
   - import it in `models/__init__.py`
   - add ACLs in `security/ir.model.access.csv`
   - add record rules if portal or cross-user visibility matters
   - add views/actions/menus only if the feature is actually user-facing
3. If adding new XML:
   - register it in `__manifest__.py`
   - ensure load order is valid for inherited views/templates and referenced XML ids
4. If extending standard business flows:
   - anchor to standard Odoo models first
   - keep custom logic additive and inspectable

## Structural rules

- Keep one inherited model per file where practical.
- Separate backend views, portal templates, reports, and security.
- Keep methods small and override-friendly.
- Do not introduce side-channel state when a standard field or relation already exists.

## What to avoid

- Do not silently redefine the meaning of an existing standard or already-used custom field.
- Do not hardcode business truth into views only; enforce rules in models.
- Do not count a field/menu/button as implementation if the actual workflow is not wired through.
- Do not add placeholder actions that only notify or log when the feature is meant to execute real business behavior.

## Validation

Run at minimum:

```bash
python3 -m py_compile custom_addons/automotive_parts/models/*.py custom_addons/automotive_parts/controllers/*.py
find custom_addons/automotive_parts -name '*.xml' -print0 | xargs -0 -n1 xmllint --noout
```

If the task changes installed behavior, also plan for a real module upgrade and UI test in the running Odoo instance.
