# Railway Deployment (Self-Hosted Odoo)

This project now includes a Railway-ready container setup:

- `Dockerfile`
- `railway.json`
- `scripts/railway_start.sh`

## 1. Create Railway resources

1. Create a new Railway project.
2. Add a **PostgreSQL** service.
3. Add a **Volume** and mount it at `/data` (required for filestore persistence).
4. Connect this Git repository as a new service (Railway will detect `Dockerfile`).

## 2. Configure environment variables

Set these on the Odoo service:

- `DATABASE_URL` = reference the Railway Postgres service connection string
- `ODOO_ADMIN_PASSWD` = strong random string
- `ODOO_DB_NAME` = your DB name (usually same as Railway `PGDATABASE`)
- `ODOO_DB_USER` = dedicated app DB user (do not use `postgres` on Odoo 18)
- `ODOO_DB_PASSWORD` = password for `ODOO_DB_USER`
- `ODOO_LIST_DB` = `False`
- `ODOO_PROXY_MODE` = `True`
- `ODOO_WORKERS` = `0` (start with this; increase later if needed)
- `ODOO_MAX_CRON_THREADS` = `1`
- `ODOO_INIT_DB` = `true` (first deploy only; auto-initializes DB if missing)
- `ODOO_INIT_MODULES` = `base,web,automotive_parts` (first deploy only)

Recommended:

- keep `ODOO_AUTO_UPDATE_MODULES=false` on the live web service
- run module upgrades as an explicit maintenance step, not inline on every startup
- if you temporarily enable it for first boot, turn it off again immediately after the upgrade finishes

App secrets used by the custom module:

- `RAPIDAPI_KEY`
- `ANAF_EFACTURA_ENV`
- `ANAF_EFACTURA_CUI`
- `ANAF_OAUTH_CLIENT_ID`
- `ANAF_OAUTH_CLIENT_SECRET`
- `ANAF_OAUTH_REDIRECT_URI`
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (example: `gpt-4o-mini`)

Railway PostgreSQL variables (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, or `DATABASE_URL`) are read automatically by `scripts/railway_start.sh`, but Odoo 18 refuses DB user `postgres`. Override with `ODOO_DB_USER` / `ODOO_DB_PASSWORD` for the application connection.

Tip: start from `.env.railway.example` and copy values into Railway Variables UI.

## 3. Deploy and initialize

1. Trigger deploy.
2. Open service logs and wait for Odoo to start on `$PORT`.
3. Open your Railway public domain in browser.
4. If DB is missing and `ODOO_INIT_DB=true`, startup initializes it automatically.
5. If you restore a dump instead, keep `ODOO_INIT_DB=false`.
6. In Apps, update apps list and install/upgrade `automotive_parts` when needed.

The service now declares `healthcheckPath=/web/login` in `railway.json` so Railway waits for a real HTTP 200 page before treating the deployment as healthy.

After first successful boot, disable one-time init/update:

- `ODOO_INIT_DB=false`
- `ODOO_AUTO_UPDATE_MODULES=false`

## 4. Operational notes

- Persistence:
  - PostgreSQL keeps relational data.
  - `/data` volume keeps Odoo filestore (attachments).
- If you scale to multiple replicas, keep a shared filestore volume or object storage strategy.
- Keep `ODOO_LIST_DB=False` in production.
- Put Railway service behind a custom domain and enforce HTTPS.

## 5. CI/CD

Recommended flow:

1. Open Railway service settings for the app service.
2. Enable **Wait for CI** on the connected GitHub branch.
3. Use GitHub Actions workflow `.github/workflows/ci.yml` as the required CI check.
4. Merge to `main` only after CI passes.
5. Railway auto-deploys `main` after GitHub reports success.

What CI validates:

- Python syntax for the custom addon
- XML parsing for addon data/views/security files
- invoice ingest frontend bundle build
- Docker image build

What CD does:

- Railway rebuilds and redeploys the app service from `main`
- production DB remains in place
- schema changes must still be applied by upgrading `automotive_parts`

Suggested environments:

- `main` -> production
- separate Railway environment/service -> staging

## 5. Known constraints

- This is self-hosted deployment. Odoo Enterprise license is still required if you use Enterprise edition features.
- `wkhtmltopdf` is not bundled in this container by default; PDF rendering may be limited depending on report usage.
