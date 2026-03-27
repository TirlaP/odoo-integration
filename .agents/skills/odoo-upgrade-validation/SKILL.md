---
name: odoo-upgrade-validation
description: Use when shipping Odoo addon changes that must survive module upgrade, inherited-view resolution, and existing databases. Covers upgrade-safe practices and the minimum validation workflow.
---

# Odoo Upgrade and Validation

Use this skill when a change is meant to be deployed on an existing Odoo database.

## Upgrade-safe rules

- Never assume a clean install only. Existing XML ids, fields, and menus matter.
- Keep inherited view anchors exact and minimal.
- Do not rename model names, field names, or XML ids casually.
- If a field meaning changes, migrate deliberately instead of silently repurposing it.
- If a feature depends on data files, make sure manifest load order matches the inheritance/reference chain.

## Required validation

Always run:

```bash
python3 -m py_compile custom_addons/automotive_parts/models/*.py custom_addons/automotive_parts/controllers/*.py
find custom_addons/automotive_parts -name '*.xml' -print0 | xargs -0 -n1 xmllint --noout
```

For changes that affect installed behavior, also do:

1. module upgrade in the running instance
2. open each changed backend form/tree/search view
3. execute the core happy path
4. execute one permission-sensitive path
5. execute one existing-record upgrade path

## Upgrade-sensitive hotspots

- inherited views/templates
- record rules
- computed stored fields
- data XML with `noupdate` assumptions
- cron jobs
- report templates
- renamed or re-scoped menus/actions

## Minimum release note for each change

Track:

- models added/changed
- views added/changed
- new ACL/rule impact
- required config or dependency changes
- whether real module upgrade/browser verification still remains
