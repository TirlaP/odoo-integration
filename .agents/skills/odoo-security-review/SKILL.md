---
name: odoo-security-review
description: Security review for Odoo customizations in this repository. Use for access-control audits, portal review, controller review, unsafe sudo usage, data leakage, injection risks, document exposure, and accounting/stock security boundaries.
---

# Odoo Security Review

Review the code for exploitable or high-confidence security issues.

## Focus areas

- ACL gaps in `ir.model.access.csv`
- missing or overbroad record rules
- unsafe `sudo()` usage
- portal controllers exposing data across partners
- document/archive attachment exposure
- insecure report or file download routes
- command execution or printer dispatch paths
- secrets/config leakage in logs or audit entries
- raw SQL or unsafe eval/exec/subprocess usage

## Odoo-specific checks

1. **Model access**
   - Does every new model have explicit ACLs?
   - Are portal ACLs intentionally narrow?

2. **Record rules**
   - Are rules scoped through the correct relation?
   - Do group rules and global rules compose safely?
   - Could a mechanic or customer see another partner’s records?

3. **Portal**
   - No trust in URL ids alone; domain-filter every record fetch.
   - Avoid `sudo()` in portal paths.
   - If templates dereference related models, confirm those models are portal-readable too.

4. **Attachments and documents**
   - Check whether linked `ir.attachment` or custom archive models respect company and ownership boundaries.

5. **Sensitive operations**
   - Printing, file generation, imports, and external API calls should not accept unsafe user-controlled paths/commands.

## Reporting standard

Report only concrete, high-confidence issues or clearly risky access-control flaws. Distinguish:

- exploitable issue
- risky but needs verification
- non-issue due to standard Odoo protection
