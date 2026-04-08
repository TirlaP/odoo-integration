# Railway Deployment (Self-Hosted Odoo)

This project now includes a Railway-ready container setup:

- `Dockerfile`
- `railway.json`
- `scripts/railway_start.sh`
- `scripts/railway_migrate.sh`

## 1. Create Railway resources

1. Create a new Railway project.
2. Add a **PostgreSQL** service.
3. Add a **Volume** and mount it at `/data` (required for filestore persistence).
4. Connect this Git repository as a new service (Railway will detect `Dockerfile`).

Do not skip the `/data` volume. Odoo attachments are stored in the filestore by default. Without a persistent volume, OCR imports can keep the attachment row in PostgreSQL while losing the actual PDF/image bytes on redeploy or restart.

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

The service now declares `healthcheckPath=/web/health` in `railway.json` so Railway probes Odoo's dedicated no-auth readiness endpoint instead of the login flow.

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

1. Protect `main` in GitHub and require the `CI / validate` check.
2. Create a second Railway service or Railway environment for staging.
3. Track `staging` on the staging service and `main` on the production service.
4. Enable **Wait for CI** on both Railway tracked branches.
5. Merge feature branches into `staging` first, verify the staging deployment, then merge `staging` into `main`.
6. Railway auto-deploys the tracked branch only after GitHub reports success.

What CI validates:

- Python syntax for the custom addon
- XML parsing for addon data/views/security files
- invoice ingest frontend bundle build
- Docker image build
- Odoo regression tests via `scripts/run_odoo_tests.sh`

What CD does:

- Railway rebuilds and redeploys the tracked service from `staging` or `main`
- production DB remains in place
- schema changes must still be applied by upgrading `automotive_parts`
- GitHub Actions workflow `.github/workflows/post-deploy.yml` runs a smoke test against the Railway deployment URL after Railway marks the deployment successful

## 6. Observability

Use three layers:

1. Railway logs
- Odoo already starts with `--logfile -`, so app logs go to stdout/stderr
- PostgreSQL service logs remain available from the Railway Postgres service
- use Railway as the raw infrastructure log collector first

2. Odoo Runtime Logs
- the addon now exposes `Runtime Logs` in the backend UI
- this captures high-signal runtime failures such as browser diagnostics, HTTP exceptions, cron failures, and async job failures
- retention is controlled by `automotive.runtime_log_retention_days` and defaults to 30 days

3. Optional Sentry
- use Sentry for exception tracking, stack traces, frontend Owl/browser errors, and performance traces
- do not treat Sentry as the primary storage for raw DB or infrastructure logs
- smallest practical stack is `Railway logs + Runtime Logs + optional Sentry`

4. Optional Better Stack
- use Better Stack as the centralized app log panel when you want time-window queries outside Railway
- create an HTTP source and set:
  - `BETTER_STACK_SOURCE_TOKEN`
  - `BETTER_STACK_INGESTING_HOST`
- the addon runtime logger forwards structured JSON events there when both env vars are present
- start with the Odoo app service first; keep PostgreSQL logs in Railway until you decide whether you also want a separate DB-log pipeline

## 7. Production-safe migrations

Do not rely on `ODOO_AUTO_UPDATE_MODULES=true` on the web service in production. It delays HTTP startup and can fail Railway healthchecks while Odoo is still migrating.

Use a dedicated migration step instead:

1. Keep the normal web service start command unchanged.
2. In Railway, set the service **Pre-deploy Command** to:

```bash
/app/scripts/railway_migrate.sh
```

3. Set:

- `ODOO_UPDATE_MODULES=automotive_parts`

This runs the Odoo module upgrade before the new deployment goes live, without tying schema updates to the web server healthcheck window.

Important Railway constraint:

- pre-deploy commands run in a separate container
- volumes are not mounted there
- do not make pre-deploy logic depend on `/data` filestore contents
- keep pre-deploy limited to database-only migrations and module upgrades

Manual fallback:

- open a Railway shell for the app service and run:

```bash
/app/scripts/railway_migrate.sh
```

The script reads the same Railway env vars as the normal start flow and runs:

```bash
python3 /app/odoo/odoo-bin -c /tmp/odoo-railway.conf -d "$ODOO_DB_NAME" -u "$ODOO_UPDATE_MODULES" --stop-after-init --no-http --logfile -
```

Suggested environments:

- `main` -> production
- `staging` -> staging

## 8. What This Repo Now Supports

Repo-side CI/CD for Railway is now:

- `.github/workflows/ci.yml`
  - runs on pull requests, `staging`, and `main`
  - validates Python, XML, frontend build, Odoo tests, and Docker build
- `.github/workflows/post-deploy.yml`
  - runs on Railway `deployment_status=success`
  - smoke-tests `/web/health` and `/web/login`
- `scripts/smoke_test.sh`
  - reusable smoke test for Railway deployments

This is the DIY version of the Odoo.sh/Skysize workflow:

- feature branch -> pull request -> CI
- merge to `staging` -> Railway staging deploy -> smoke test
- merge to `main` -> Railway production deploy -> smoke test
- run `/app/scripts/railway_migrate.sh` as pre-deploy for schema changes

## 9. Known constraints

- This is self-hosted deployment. Odoo Enterprise license is still required if you use Enterprise edition features.
- `wkhtmltopdf` is not bundled in this container by default; PDF rendering may be limited depending on report usage.
