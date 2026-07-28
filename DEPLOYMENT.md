# BarcodeNest deployment

This runbook prepares the API for a provider-neutral, single-instance public
deployment. It does not deploy infrastructure or change DNS.

## Intended architecture

```text
barcodenest.com      -> separately hosted static website
api.barcodenest.com  -> FastAPI container
private PostgreSQL   -> products, API clients, keys, and usage
support@barcodenest.com -> external mail provider
```

Cloudflare can provide DNS, proxying, and TLS later. The marketing/developer
website and email service are not part of the API image.

## A. Required environment variables

Create a local `.env.production` file. It is excluded by `.dockerignore` and
`.gitignore`; never commit it.

```text
APP_ENV=production
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
API_KEY_HASH_SECRET=replace-with-at-least-32-random-characters
AUTO_CREATE_TABLES=false
PORT=8000
LOG_LEVEL=INFO
CORS_ALLOWED_ORIGINS=https://barcodenest.com,https://www.barcodenest.com
TRUSTED_HOSTS=api.barcodenest.com,localhost,127.0.0.1,YOUR_PROVIDER_HOSTNAME
FORWARDED_ALLOW_IPS=127.0.0.1
```

Generate `API_KEY_HASH_SECRET` with a secure secret manager or:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store the result in the deployment provider's secret store. Keep it stable:
changing it invalidates every existing API key. URL-encode special characters
in the database username/password. `TRUSTED_HOSTS` contains hostnames only,
without schemes or paths. Include the provider hostname during initial
deployment and remove it later if it is no longer needed.

`CORS_ALLOWED_ORIGINS` is relevant only to browser clients. Server-to-server
clients do not need CORS. Do not use `*` in production.

`FORWARDED_ALLOW_IPS` controls which reverse proxies Uvicorn trusts for
forwarded client/protocol information. Set it to the provider's documented
proxy address or network; do not broaden it blindly.

Optional settings are documented in `.env.example`, including `BATCH_LIMIT`
and the JSON `PLAN_LIMITS` override.

## B. Build the Docker image

```powershell
docker build --tag barcodenest-api:local .
```

The image uses Python 3.12 slim, locked production dependencies, a non-root
runtime user, and no development reload server. The context excludes `data/`,
database files, dumps, `.venv`, `.env*`, logs, tests, and quality reports.

## C. Run locally in production mode

When PostgreSQL is running directly on the Windows host, use
`host.docker.internal` as the database hostname in `.env.production`:

```text
DATABASE_URL=postgresql+psycopg://grocery:PASSWORD@host.docker.internal:5432/grocery_api
```

Then run:

```powershell
docker run --rm --name barcodenest-api `
  --env-file .env.production `
  --publish 8000:8000 `
  barcodenest-api:local
```

The container listens on `0.0.0.0:$PORT`. It intentionally runs one Uvicorn
worker because the current per-minute rate limiter is process-local.

## D. Run database migrations

Migrations are an explicit release step and are never run by API startup:

```powershell
$env:DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE"
.\.venv\Scripts\alembic.exe upgrade head
```

Alternatively, run the image as a one-off migration job:

```powershell
docker run --rm --env-file .env.production `
  barcodenest-api:local alembic upgrade head
```

The migration chain remains:

```text
20260725_0001 -> 20260726_0002 -> 20260728_0003
```

Do not use `AUTO_CREATE_TABLES=true` in production.

## E. Transfer PostgreSQL with pg_dump/pg_restore

The 4.2-million-product database is not stored in Git or Docker. Transfer it
separately during a maintenance window. The following custom-format dump is
compressed and includes schema plus data:

```powershell
$env:SOURCE_PG_URL="postgresql://grocery:LOCAL_PASSWORD@localhost:5432/grocery_api"
pg_dump `
  --dbname="$env:SOURCE_PG_URL" `
  --format=custom `
  --compress=6 `
  --no-owner `
  --no-privileges `
  --file="barcodenest-production.dump"
```

Do not commit or add this dump to a Docker build. Copy it to the production
environment through the provider's secure transfer mechanism.

Create an empty production database and restore into it:

```powershell
$env:PRODUCTION_PG_URL="postgresql://USER:PASSWORD@HOST:5432/DATABASE"
pg_restore `
  --dbname="$env:PRODUCTION_PG_URL" `
  --no-owner `
  --no-privileges `
  --exit-on-error `
  --jobs=4 `
  "barcodenest-production.dump"
```

The target database must be empty. This procedure deliberately omits
`--clean` and does not delete an existing database. If the dump's migration
version is older than the application, run `alembic upgrade head` after the
restore.

Validate the restored database:

```powershell
psql "$env:PRODUCTION_PG_URL" -c "SELECT count(*) AS product_count FROM products;"
psql "$env:PRODUCTION_PG_URL" -c "SELECT version_num FROM alembic_version;"
psql "$env:PRODUCTION_PG_URL" -c "SELECT to_regclass('public.api_clients') AS api_clients, to_regclass('public.api_keys') AS api_keys, to_regclass('public.monthly_usage') AS monthly_usage;"
```

For the current source database, expect approximately 4.2 million products and
Alembic version `20260728_0003`.

## F. Start the API

Run the migration first, then start exactly one container replica using the
command from section C. Do not use `--reload` in production.

The image health check calls the public `/health` endpoint every 30 seconds.

## G. Health check

```powershell
curl.exe --fail --show-error `
  "http://127.0.0.1:8000/health"
```

Expected response:

```json
{"status":"ok"}
```

## H. Test an authenticated lookup

```powershell
$env:BARCODENEST_API_KEY="gpa_copy-the-production-key-here"
curl.exe --fail --show-error `
  --header "X-API-Key: $env:BARCODENEST_API_KEY" `
  "http://127.0.0.1:8000/v1/products/3017620422003"
```

Swagger is available at `/docs`, and its **Authorize** control sends the same
`X-API-Key` header. `/health` remains public; `/v1/products/...` and the batch
route remain protected.

## I. Create the first production client and key

Run against the migrated production database from a trusted administration
environment:

```powershell
$env:DATABASE_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE"
$env:API_KEY_HASH_SECRET="the-same-secret-used-by-the-api"
$env:APP_ENV="production"
$env:AUTO_CREATE_TABLES="false"

.\.venv\Scripts\python.exe -m app.tools.api_keys create-client `
  --identifier production-test `
  --name "Production test client" `
  --plan FREE

.\.venv\Scripts\python.exe -m app.tools.api_keys create-key `
  --client production-test `
  --name initial
```

The raw key is shown once. Store it in a password/secret manager. Only its
non-secret prefix and HMAC-SHA-256 digest are stored in PostgreSQL.

## J. Domain

The expected public API hostname is:

```text
https://api.barcodenest.com
```

Add it to `TRUSTED_HOSTS`. Configure the provider health check for `/health`.

## K. Cloudflare later

Cloudflare DNS/proxy/TLS configuration is a separate future operation. Do not
point DNS until the container, database connectivity, migrations, health check,
and authenticated lookup have all passed using the provider hostname.

## Security and scaling notes

- FastAPI debug mode is disabled and production responses do not expose stack
  traces.
- Raw API keys and database URLs are not logged by application code.
- Production startup rejects placeholder/short API-key secrets,
  `AUTO_CREATE_TABLES=true`, and non-PostgreSQL database URLs.
- Keep `.env.production`, dumps, database credentials, and API keys out of Git.
- Monthly quota updates are atomic and PostgreSQL-backed.
- The per-minute limiter is memory-local. Multiple workers, replicas, or hosts
  would each enforce an independent limit. Before scaling beyond one process,
  replace it with a shared implementation such as Redis.
