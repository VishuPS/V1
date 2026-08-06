# Grocery & Retail Product Barcode API

A production-oriented MVP that turns a validated EAN/UPC/GTIN barcode into
normalized product JSON from a local database. It supports EAN-8, UPC-A,
EAN-13, and GTIN-14, including check-digit validation and equivalent UPC/EAN
representations.

The customer-facing API never calls Open Food Facts during a lookup:

```text
Open Food Facts JSONL export -> streaming normalization -> batched upserts
-> local database -> FastAPI -> clients
```

The SQLAlchemy repository layer isolates persistence from routes and services.
SQLite is the default for local development; changing `DATABASE_URL` to a
PostgreSQL SQLAlchemy URL does not require an application redesign.

## Requirements and installation

- Python 3.12+

```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy the
environment file with `cp .env.example .env`.

Configuration is read from environment variables or `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./products.db` | SQLAlchemy database URL |
| `AUTO_CREATE_TABLES` | `true` | Convenience table creation; set false when Alembic manages production |
| `CORS_ALLOWED_ORIGINS` | local ports 3000 and 5173 | Comma-separated origins |
| `BATCH_LIMIT` | `100` | Maximum items per batch |
| `LOG_LEVEL` | `INFO` | Application logging level |
| `APP_ENV` | `development` | Environment label |
| `OPEN_FOOD_FACTS_DATASET_URL` | official JSONL gzip export | Explicit download source |
| `INGESTION_BATCH_SIZE` | `1000` | Records committed per ingestion batch |
| `API_KEY_HASH_SECRET` | development-only placeholder | Stable server-side secret used to HMAC API keys |
| `PLAN_LIMITS` | FREE/STARTER/GROWTH defaults | JSON object defining monthly API-call and per-minute request limits |

Do not configure `CORS_ALLOWED_ORIGINS=*` for production. Use the exact client
origins instead. In production, replace `API_KEY_HASH_SECRET` with a strong,
random secret and keep it stable; changing it invalidates all issued keys.

## Developer API authentication and usage controls

`GET /health` is public. Both product lookup endpoints require an API key in
the `X-API-Key` header. Keys are generated from high-entropy random material;
the raw value is displayed once and only an HMAC-SHA-256 digest plus a
non-secret lookup prefix is stored. Raw keys cannot be recovered from the
database.

The built-in defaults are:

| Plan | Monthly barcode lookups | Requests per minute |
|---|---:|---:|
| `FREE` | 250 | 30 |
| `STARTER` | 2,000 | 300 |
| `GROWTH` | 5,000 | 1,200 |

A single lookup costs one lookup. A batch costs one lookup per submitted
barcode and one request total, preventing batch requests from bypassing the
monthly quota. Usage is stored as one aggregate row per key and calendar month,
with atomic database increments. It does not create one row per request.

For PowerShell, configure a stable secret and apply migrations before issuing
keys:

```powershell
$env:API_KEY_HASH_SECRET="replace-with-a-long-random-secret"
.\.venv\Scripts\alembic.exe upgrade head

.\.venv\Scripts\python.exe -m app.tools.api_keys create-client `
  --identifier demo --name "Demo client" --plan FREE
.\.venv\Scripts\python.exe -m app.tools.api_keys create-key `
  --client demo --name local-development
```

Copy the raw key when it is printed; it will not be shown again. Start the API
and make authenticated requests:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload

# Expected: 401 missing_api_key
Invoke-RestMethod http://127.0.0.1:8000/v1/products/3017620422003

$headers = @{"X-API-Key" = "paste-the-raw-key-here"}
Invoke-RestMethod http://127.0.0.1:8000/v1/products/3017620422003 `
  -Headers $headers
```

Swagger UI at `http://127.0.0.1:8000/docs` exposes an **Authorize** control for
the same header. Administrative operations are CLI-only and require trusted
database access:

```powershell
.\.venv\Scripts\python.exe -m app.tools.api_keys list-keys --client demo
.\.venv\Scripts\python.exe -m app.tools.api_keys usage --client demo
.\.venv\Scripts\python.exe -m app.tools.api_keys change-plan `
  --client demo --plan STARTER
.\.venv\Scripts\python.exe -m app.tools.api_keys revoke `
  --key-prefix gpa_prefix-shown-by-list-keys
.\.venv\Scripts\python.exe -m app.tools.api_keys reactivate `
  --key-prefix gpa_prefix-shown-by-list-keys
```

Override plan limits with JSON, for example:

```text
PLAN_LIMITS={"FREE":{"monthly_lookups":250,"requests_per_minute":30},"STARTER":{"monthly_lookups":2000,"requests_per_minute":300},"GROWTH":{"monthly_lookups":5000,"requests_per_minute":1200}}
```

Monthly quota enforcement is durable and safe across application instances.
The minute limiter is intentionally process-local for this MVP: it resets when
the process restarts and is not shared across multiple workers or hosts. Before
a multi-instance cloud deployment, replace it with a shared Redis-backed
limiter. The project currently has no self-service key portal, billing,
payments, or automatic key rotation; those are intentionally outside this MVP.

## Database setup and migrations

SQLite remains the zero-service development default. The canonical GTIN-14
`barcode` primary key supplies a unique B-tree lookup index on both SQLite and
PostgreSQL; additional indexes cover source-barcode type and provenance.

For large imports, start only PostgreSQL with the provided Compose file:

```bash
docker compose up -d postgres
```

Configure the application:

```text
DATABASE_URL=postgresql+psycopg://grocery:grocery-local@localhost:5432/grocery_api
AUTO_CREATE_TABLES=false
```

Apply the schema:

```bash
alembic upgrade head
```

The `20260726_0002` migration changes externally supplied product text
(`name`, `brand`, `quantity`, and `image_url`) from restrictive `VARCHAR`
columns to PostgreSQL/SQLite `TEXT`. This preserves real Open Food Facts values
instead of truncating them. Controlled identifiers remain bounded:
`barcode` is 14 characters, `barcode_type` is 16, `source` is 128, and
`source_id` is 256.

If a previous large import stopped with `StringDataRightTruncation`, keep the
already committed rows, apply `alembic upgrade head`, and rerun the same import
command. Canonical-GTIN upserts make the rerun idempotent. Database `DataError`
failures caused by an individual source record are isolated to that record and
reported with its barcode and field lengths; connectivity and other systemic
database failures still stop the job.

For a pre-existing SQLite database created before Alembic was added, first
back it up, verify it matches the current model, then record the baseline with
`alembic stamp head`. New databases should always use `alembic upgrade head`.
Generate future migrations with `alembic revision --autogenerate -m "message"`.

### Local production-like evaluation (Windows PowerShell)

Install Docker Desktop first and ensure `docker version` succeeds. If it does
not, install/start Docker Desktop before continuing; the application cannot
create a PostgreSQL service by itself.

```powershell
# 1. Start PostgreSQL
$env:POSTGRES_PASSWORD="choose-a-local-password"
docker compose up -d postgres

# 2. Check container state and health
docker compose ps
docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q postgres)"

# 3. Point this shell at PostgreSQL
$env:DATABASE_URL="postgresql+psycopg://grocery:$($env:POSTGRES_PASSWORD)@localhost:5432/grocery_api"
$env:AUTO_CREATE_TABLES="false"

# 4. Apply migrations and verify the database
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.tools.db_check

# 5. Download the official bulk export without importing it
.\.venv\Scripts\python.exe -m app.ingestion.openfoodfacts `
  --download --download-only `
  --source-file data/openfoodfacts-products.jsonl.gz

# 6. Import/evaluate 100k, then 1M source records
.\.venv\Scripts\python.exe -m app.ingestion.openfoodfacts `
  --source-file data/openfoodfacts-products.jsonl.gz --limit 100000 --batch-size 1000
.\.venv\Scripts\python.exe -m app.tools.data_quality `
  --json-output database-quality-100k.json

.\.venv\Scripts\python.exe -m app.ingestion.openfoodfacts `
  --source-file data/openfoodfacts-products.jsonl.gz --limit 1000000 --batch-size 2000
.\.venv\Scripts\python.exe -m app.tools.data_quality `
  --json-output database-quality-1m.json

# 7. Supply an independent benchmark, then retain comparable summaries
.\.venv\Scripts\python.exe -m app.tools.coverage data\real-barcode-benchmark.csv `
  --independent --output coverage-1m.csv --json-output coverage-1m.json

# 8. Benchmark lookups at the current product count
.\.venv\Scripts\python.exe -m app.tools.performance --iterations 5000 --batch-size 50

# 9. Start the API against PostgreSQL
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open Swagger at <http://127.0.0.1:8000/docs>. Stop PostgreSQL without deleting
data using `docker compose stop postgres`. The following reset is destructive
and permanently deletes the local PostgreSQL volume:

```powershell
docker compose down -v
```

## Initialize and import sample data

The import command creates the database tables and upserts two bundled sample
products:

```bash
python -m app.ingestion.openfoodfacts
```

The importer accepts newline-delimited JSON in plain, gzip, bzip2, or xz form.
Small JSON-array fixtures are also supported. Every source code passes through
the existing GTIN validator. Internally products use a zero-padded GTIN-14 key,
while `source_id` retains the original Open Food Facts code for provenance.

### Obtain and import the official dataset

Open Food Facts publishes a daily bulk JSONL export at:

<https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz>

The export is several gigabytes. It is never downloaded during API startup or
tests. Download it only when intended:

```bash
python -m app.ingestion.openfoodfacts \
  --download \
  --download-only \
  --source-file data/openfoodfacts-products.jsonl.gz \
```

The downloader writes to a `.part` file, resumes it when the server accepts HTTP
range requests, reports progress and final size, and atomically renames it on
success. An existing plausible dataset is kept; use `--force-download` only to
replace it deliberately. To import 10,000 source records after downloading:

```bash
python -m app.ingestion.openfoodfacts \
  --source-file data/openfoodfacts-products.jsonl.gz \
  --limit 10000 \
  --batch-size 1000
```

Larger runs use the identical bounded-memory path:

```bash
# 100,000 source records
python -m app.ingestion.openfoodfacts --source-file data/openfoodfacts-products.jsonl.gz --limit 100000 --batch-size 1000

# 1,000,000 source records
python -m app.ingestion.openfoodfacts --source-file data/openfoodfacts-products.jsonl.gz --limit 1000000 --batch-size 2000

# Full dataset
python -m app.ingestion.openfoodfacts --source-file data/openfoodfacts-products.jsonl.gz --batch-size 2000
```

Omit `--limit` for a full import. The file is streamed, malformed/unusable
records are counted and skipped, and valid products are committed in batches.
At completion the CLI prints processed, inserted, updated, skipped, barcode
quality, error, elapsed-time, throughput, and resulting database product count.
Imports are idempotent: existing canonical GTINs are updated instead of
duplicated. PostgreSQL is strongly recommended for million-record runs.

### Backfill Open Food Facts images

Current Open Food Facts JSONL exports store image metadata primarily under
`images.selected.front.<language>` and `images.uploaded`, rather than always
providing a top-level `image_url`. To populate images on an existing database
without rewriting every product field:

```powershell
.\.venv\Scripts\python.exe -m app.ingestion.openfoodfacts `
  --source-file data/openfoodfacts-products.jsonl.gz `
  --update-images-only `
  --batch-size 5000
```

This streams the existing compressed export, derives documented Open Food Facts
image URLs from its metadata, and performs batched `UPDATE` statements only
where a matching canonical GTIN has a different image URL. It does not insert
products, clear existing images when the export has none, or call the live Open
Food Facts API. The operation is idempotent and may be safely rerun.

Image selection priority is: explicit front URL when present; selected front
image in the product language, then English, then another available language;
finally the most recently uploaded image when no front is selected. A 400px
rendition is preferred for API use, with full size used only when 400px is not
available.

Each source implements `ProductSource`; future source adapters can feed the same
normalizer without changing the API. The database retains source name, original
source barcode/identifier, source update time, and local creation/update times.

## Run locally

```bash
uvicorn app.main:app --reload
```

- Health: <http://127.0.0.1:8000/health>
- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

## API examples

Single lookup:

```bash
curl http://127.0.0.1:8000/v1/products/3017620422003
```

```json
{
  "barcode": "3017620422003",
  "barcode_type": "EAN-13",
  "canonical_gtin": "03017620422003",
  "valid": true,
  "found": true,
  "product": {
    "name": "Nutella",
    "brand": "Ferrero",
    "categories": ["en:spreads", "en:hazelnut-spreads"],
    "quantity": "400 g",
    "image_url": null,
    "ingredients": "Sugar, palm oil, hazelnuts, skimmed milk powder, cocoa, lecithins, vanillin.",
    "allergens": ["en:milk", "en:nuts", "en:soybeans"],
    "nutrition": {"energy-kcal_100g": 539, "fat_100g": 30.9},
    "countries": ["en:france", "en:portugal"]
  },
  "source": {"name": "Open Food Facts", "source_id": "3017620422003"},
  "error": null
}
```

Batch lookup:

```bash
curl -X POST http://127.0.0.1:8000/v1/products/batch \
  -H "Content-Type: application/json" \
  -d '{"barcodes":["3017620422003","4006381333931","123"]}'
```

Single lookups return `400` for an invalid barcode, `404` for a valid but
unknown product, `200` when found, and structured `500` errors on server or
database failure. Batch requests return one result per input; invalid and
unknown entries do not fail the whole batch. Empty or over-limit batches return
`422`.

## Tests

```bash
pytest
```

Tests cover all supported barcode formats, invalid check digits, normalization,
equivalent representations, found/unknown/invalid lookups, mixed batches,
limits, streaming compressed ingestion, quality normalization, invalid record
skipping, idempotent updates, post-import API lookup, coverage metrics, request
errors, and database failure handling. Tests use only small local fixtures and
never download a dataset.

## Independent coverage benchmark

The benchmark CSV schema is:

```text
barcode,expected_product_name,expected_brand,country,category,source_of_barcode
```

`data/sample_benchmark.csv` contains only four explicitly labelled
development examples. It is not evidence of market coverage. A meaningful
commercial assessment requires independently observed grocery barcodes from
Portugal, the UK, and broader EU markets, spread across relevant categories.
Do not build the benchmark by sampling the imported Open Food Facts database;
that would bias hit rate upward.

Copy `data/real_barcode_benchmark_template.csv` to
`data/real-barcode-benchmark.csv` and replace every fictional row with an
independent observation. Only `barcode` is required. Optional columns are
`expected_product_name`, `expected_brand`, `country`, `category`,
`retailer_or_source`, and `notes`.

```bash
python -m app.tools.coverage benchmark.csv \
  --independent \
  --output coverage-report.csv \
  --json-output coverage-summary.json
```

The terminal and JSON summaries report overall validity/hit rate, all requested
field-completeness percentages, and hit-rate breakdowns by benchmark country,
category, and barcode type. The loader reports malformed rows, missing optional
metadata, invalid/unsupported values, and equivalent duplicate barcodes. The
first occurrence of a canonical GTIN is retained and later duplicates are
ignored so they cannot distort hit rate. Missing optional product fields reduce
completeness but do not turn a successful lookup into a miss.

Use `--independent` only when the rows were genuinely collected outside this
database. Without that explicit confirmation, the report states that commercial
coverage cannot be classified.

Interpret an independent benchmark as a signal, not an automatic launch
decision:

- at least 80% hit rate: strong signal to proceed, subject to field completeness;
- 60–80%: potentially viable; investigate gaps and missing fields;
- below 60%: improve coverage before commercial launch.

## Database quality report

This measures completeness *inside the imported database*, not real-world hit
rate:

```bash
python -m app.tools.data_quality --json-output database-quality.json
```

It reports all requested field percentages, source barcode-type counts, and
top country/category tags while streaming ORM rows in bounded batches. High
database completeness does not prove that products encountered by customers
are present.

## Lookup performance benchmark

After importing products:

```bash
python -m app.tools.performance --iterations 1000 --batch-size 50
```

This lightweight local benchmark reports mean, median, p95, and p99 latency for
known products, unknown valid GTINs, repeated lookups, and mixed batches. It
measures the current machine/database configuration and is not a substitute for
production load testing.

## Data attribution and licensing

The bundled sample records are adapted from **Open Food Facts** for development.
Open Food Facts database content is available under the Open Database License
(ODbL). Database reuse must attribute Open Food Facts and may trigger ODbL
share-alike obligations for a publicly used adapted database. Individual
database contents are available under the Database Contents License. Product
images have their own licensing and attribution considerations (commonly
Creative Commons Attribution-ShareAlike); an image URL in this database does
not transfer image ownership or eliminate those obligations.

Review the current [Open Food Facts reuse documentation](https://support.openfoodfacts.org/help/en-gb/12-api-data-reuse/71-how-to-reuse-open-food-facts-data)
and [official bulk-data guidance](https://openfoodfacts.github.io/openfoodfacts-python/usage/)
before redistributing or commercializing imported data. Seek legal advice for
your particular commercial use.

Third-party product data remains attributed to its provider. This project does
not claim ownership of Open Food Facts data. Provenance is visible in both the
database (`source`, `source_id`, `source_updated_at`) and API responses.
No partnership with or endorsement by Open Food Facts is implied.
