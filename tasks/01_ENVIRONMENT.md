# 01) Environment & project hygiene

## 1.1 Local dev commands (repo)

- [ ] Start Odoo with logs: `./dev start -d <db_name>`
- [ ] Update module after changes: `./dev update -d <db_name>`
- [ ] Tail logs: `./dev logs`

Acceptance:
- Odoo opens in browser.
- Module `automotive_parts` installs/updates without errors.

## 1.2 Odoo config sanity

- [ ] Confirm `odoo.conf` has correct `addons_path` including `custom_addons`.
- [ ] Decide how you manage secrets (RapidAPI key, ANAF credentials):
  - recommended: store in Odoo UI models (`tecdoc.api`, `anaf.efactura`)
  - do not commit keys to git / docs

Acceptance:
- No secrets in repo history/docs.
- Odoo can read the custom addon.

## 1.3 Required env vars (ANAF + OpenAI)

Set these in your shell (or deployment secret manager) before running Odoo:

- [ ] `ANAF_EFACTURA_ENV` (`test` or `prod`)
- [ ] `ANAF_EFACTURA_CUI` (company CUI, digits only)
- [ ] `ANAF_OAUTH_CLIENT_ID`
- [ ] `ANAF_OAUTH_CLIENT_SECRET`
- [ ] `ANAF_OAUTH_REDIRECT_URI`
- [ ] `ANAF_OAUTH_AUTHORIZE_URL` (optional override, defaults to ANAF official URL)
- [ ] `ANAF_OAUTH_TOKEN_URL` (optional override, defaults to ANAF official URL)
- [ ] `ANAF_EFACTURA_ACCESS_TOKEN` (optional bootstrap)
- [ ] `ANAF_EFACTURA_REFRESH_TOKEN` (optional bootstrap)
- [ ] `OPENAI_API_KEY` (for PDF AI extraction fallback)
- [ ] `OPENAI_MODEL` (optional; default currently `gpt-4o-mini`)

Acceptance:
- “Automotive Parts → ANAF e-Factura → Load from Env” fills configuration fields.
- “Extract with OpenAI” works on an `invoice.ingest.job` with attached PDF.

## 1.4 Database lifecycle (must-have)

- [ ] Decide DB name for real use (not demo).
- [ ] Add a backup routine:
  - `pg_dump` nightly
  - a “before big import” snapshot
- [ ] Define environments:
  - `dev` (breakable)
  - `staging` (import rehearsal)
  - `prod` (real)

Acceptance:
- You can restore the DB after mistakes.
