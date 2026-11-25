# Platform expansion implementation report

Status: local implementation on `codex/platform-expansion`; not pushed or deployed.
Review date: 2026-11-25 (Europe/Lisbon).
Baseline: `1e65147`. Stripe configuration and production rows are unchanged.

## Phase 1 — tested foundation

- Replaced the homepage API response hero with an explicitly illustrative product
  workspace preview. Preserved the cream, ink and lime visual identity. Added the
  requested heading/description, Free/tools CTAs and a consistent tool card grid.
- Added grouped native-disclosure navigation, a working All Tools directory,
  five solution workflow pages and an authenticated workspace overview. Only
  implemented tools are listed; inventory, assets and custom label design are not
  advertised as complete.
- Added account-based product lookup and equivalent-GTIN conversion pages using
  existing services. Internal CSV labels and enrichment now use session wrappers
  and no longer ask authenticated users to paste API keys.
- Added server entitlement snapshots, feature enforcement, expired premium
  read-only permissions and versioned customer policies. Existing accounts retain
  legacy Creator storage; new Creator accounts receive 500 records. Existing
  integration allowances and Stripe billing logic are unchanged.
- Added a public verified-statistics snapshot endpoint and explicit operator
  refresh command. There is no count on the visitor request path. Until a verified
  snapshot is populated, the UI shows an unavailable count rather than inventing
  one. The audit's production COUNT timed out and was not repeated.
- Added configurable frontend API origin for isolated local integration testing.

Validation: 397 API tests passed; Astro reported zero errors/warnings/hints;
production-format static build and SEO checks passed for 193 pages. Mocked local
browser checks passed at 390/768/1280 px for homepage, grouped navigation, Escape
dismissal, directory, workspace, solutions and lookup/conversion pages. The
homepage preview was visually inspected. A Windows sandbox EPERM interrupted one
build; rebuilding locally outside the sandbox succeeded.

## Migration

`20261125_0024_workspace_foundation` adds `users.workspace_policy` and
`platform_statistics`. Existing users are backfilled as legacy; the default for
future users is platform_v 1. Upgrade/downgrade tests verified that existing user
rows survive. No production migration has been run.

## Phase 2 — catalogue, SKUs and labels

Implemented private variants, owner-unique identifiers, supplier/tags, optimistic
revisions, advanced filtering, bulk edits and bounded CSV/XLSX imports/exports.
SKU Manager reserves unique internal codes with configurable patterns and
sequences, product associations, duplicate reports and separate monthly quotas.
Label Studio provides drag/keyboard editing, millimetre dimensions, logos, four
fonts, A 4/thermal layouts, reusable templates and PDF/PNG/SVG exports. It supports
EAN-13, EAN-8, UPC-A, Code 128, Code 39 and QR, preserving quiet zones and whole
modules. Existing basic label printing remains available.

Migration `20261125_0025_catalogue_sku_labels` adds private product metadata,
identifier aliases, SKU sequences/allocations and label templates. Existing Vault
rows and billing are preserved. Formula cells, external workbook links, macros,
oversized archives and unsafe CSV exports are rejected or neutralised. Premium
checks and all ownership checks run on the server; expired accounts retain reads.
Heavy processing has a two-operation per-process bound and per-account rate limits.

Validation: the complete API regression suite passed (one opt-in PostgreSQL test
skipped in SQLite); that PostgreSQL test passed separately against an isolated
loopback container, including 20 concurrent allocations and quota rollback.
Browser workflow checks passed at 390/768/1280 px. Six barcode formats were decoded
from generated PNGs; the 50×25 mm thermal output also decoded. PDF output was
rendered and visually inspected. Added local dependencies openpyxl/defusedxml and
the MIT browser BWIP preview library; no paid service or production writes.

## Phase 3 — stock, processing and quality

Inventory has per-product balances, stock additions/removals, low-stock/out-of-stock
views, barcode/alias identification, supplier/category integration, location,
purchase/selling prices, currency-separated valuation, exports and paginated
append-only movement history. Zero-stock records can be archived. Stock changes
use an atomic conditional UPDATE and unique request receipts; product deletion
is blocked when it would discard a register. Missing purchase prices are explicit.

Bulk Studio reads bounded CSV/XLSX files (1,000 rows, 5 MB by default), processes
25-row browser-worker validation/generation chunks or one lookup/enrichment at a
time, and provides progress, pause/resume, failed-row retries, error reports,
CSV/XLSX/JSON and SVG ZIP downloads. SKU chunks and quota reservations are
idempotent. A retry of a public lookup can consume another lookup credit.
Jobs are tab-local: closing the page loses input/results; no background queue is
advertised. Enrichment fills gaps in the downloaded result and does not edit saved
products. Image generation, lookup, enrichment, SKU and processing quotas remain
separate. fflate adds local MIT ZIP assembly, with no new hosted service.

Catalogue Auditor checks eight documented rules without changing products. Its
score is 100 × (1 − failed checks / (products × 8)), with no score for an empty
catalogue. Reports include correction links and CSV/JSON. Audit rows have a
separate monthly allowance (Starter 10,000/Growth 50,000); reports stop rather than
inventing a partial score above the configured 10,000-record cap.

Migrations 0026/0027 add inventory/history and bounded operation receipts. Admin
shows register/movement counts and audit/processing usage. Added a streaming
workspace body limit before JSON parsing and PUT/PATCH CORS support for edits.
The full API suite passed, then the two new request-size/origin checks passed.
Real PostgreSQL checks confirmed idempotent delivery and exactly ten successful
withdrawals from ten units under twenty concurrent requests. Browser checks
passed for inventory/history, audit review, actual SVG/ZIP worker output and
390/768/1280 px layouts. Astro had no diagnostics;199-page build/SEO passed.

## Phase 4 — assets, acquisition pages and first-party analytics

Asset Tracker provides a separate register with unique internal asset numbers,
optional catalogue links, descriptions, locations, personnel assignments,
acquisition dates, purchase values/currencies and condition. Check-in/out,
movement, maintenance, retirement and restore actions append before/after history
and require a current revision. Idempotent request receipts prevent duplicate
transitions. Asset register capacity is Starter 2,000/Growth 10,000. Existing
records remain readable after expiry. PDF/PNG/SVG labels reuse Label Studio
rendering/templates and the existing label allowance; default labels support
Code 128 or QR. No asset record is automatically deleted.

Nine public tool landing pages cover Vault, Catalogue, SKU Manager, Inventory,
Label Studio, Bulk Studio, Auditor, Assets and Data Export. Each has unique
metadata, canonical URL, software/FAQ structured data, practical workflow,
visible FAQs, related links and subscription CTA. Sitemap/internal links and
solution pages are updated. Private pages remain noindex. Public tool cards lead
to explanations; authenticated workspace cards open tools directly. The homepage
now explains the full implemented platform and all four recurring/free tiers.
Unverified static 4.35 M claims were removed from the remaining coverage pages;
the coverage page uses the same verified-statistics snapshot as the homepage.

First-party analytics accepts only a random tab UUID, an allowlisted event name
and a tool category. Unknown fields are rejected. Stored visit identifiers are
hashed; authenticated events use an internal account link. No product content,
barcode values, keys, filenames, emails, names, IPs or user-agent strings are
stored in event rows or sent to an external analytics service. Browser events
respect DNT/GPC. Registration and Stripe activation events are server-recorded;
activation deduplication is independent of webhook deduplication. Analytics
savepoints cannot change registration/payment results. Admin has aggregate
30-day adoption/funnel counts and per-account inventory/asset/history counts in
addition to API and separate tool counters. Figures are explicitly approximate,
not historical backfills or fabricated customer totals. A bounded six-hour task
removes event rows older than 90 days, up to 10,000 rows/pass, retrying after outages.

Migrations 0028/0029 add private assets/history and the allowlisted event table.
All new tables are owner-indexed or time-indexed for their access patterns.

## Phase 5 — final verification and resource bounds

- Full local API regression: **424 passed,3 opt-in PostgreSQL tests skipped** in
  SQLite; the **3 PostgreSQL tests passed separately** against a disposable,
  loopback-only PostgreSQL 16 container. They verify concurrent SKU allocation,
  quota rollback, idempotent delivery, non-negative stock and one-winner checkout.
- Astro: **150 files,0 errors,0 warnings,0 hints**. Production-format static build and
  SEO checks passed for **209 pages**. **16 frontend tests passed**, including
  real internal links, public sitemap inclusion and private workspace exclusion.
- Browser workflows passed at 390/768/1280 px for catalogue, aliases, SKU handoff,
  template editing/keyboard positioning, print preview, inventory/history,
  audit review, real browser-worker SVG/ZIP generation, asset actions/labels,
  public metadata/FAQs/canonicals, analytics payload exclusion and DNT opt-out.
- A real loopback browser/API/PostgreSQL workflow passed product creation, SKU
  assignment, inventory delivery/valuation, asset check-out, saved label template
  and PNG download, catalogue audit and bulk SVG ZIP, including quota assertions.
  The downloaded PNG decoded as its original identifier.
- Six barcode formats decode from generated PNGs. A 50×25 mm Code 128 thermal
  label decodes. PDF output was rasterised and visually reviewed. SVG embeds
  fonts with explicit regular/bold/italic face attributes.
- Upgrade/downgrade checks preserve existing users, leading-zero private
  identifiers, product names, notes and revisions. Existing customers are
  grandfathered by policy; Stripe price/product identifiers and API allowances
  are unchanged. Signature verification and webhook/idempotency regressions pass.
- Security checks cover owner isolation, premium gates, expired reads, CSRF
  origin enforcement, PUT preflight, chunked-body size limits, formula/CSV
  injection (including formula-like column headers), archive traversal/macros/external links, safe text/logo handling,
  revisions and invalid numbering patterns. Private output uses no-store.
- Heavy operations share a two-slot per-process bound. Files are bounded by
  encoded/decompressed size, rows and columns. Rich product exports read 100 rows
  at a time and fetch aliases per batch; exports and audits stop at 12 MB of data.
  Audits can be scoped to a collection and never publish a partial overall score.
  Export construction checks size before allocating the complete output file.

Synthetic local measurements with Python allocation tracing enabled:

| Operation | Records | Seconds | Peak traced Python memory |
|---|---:|---:|---:|
| Catalogue JSON export |10,000|2.383|33.72 MiB|
| Catalogue XLSX export |10,000|22.774|20.91 MiB|
| Catalogue audit |10,000|8.938|18.35 MiB|
| PDF labels, unique identifiers |100|10.625|8.47 MiB|

These measurements use an in-memory SQLite dataset and include tracing overhead.
They are not production latency/RSS guarantees; native image allocations and
Neon network latency are outside traced Python heap measurements. Large Excel
exports are the slowest operation; smaller collections reduce work. Browser
workers keep graphics/ZIP CPU off the API. The bulk worker bundle is about 972 kB
uncompressed and is loaded only by that workspace operation.

## Modified files

See `PLATFORM_EXPANSION_FILES.txt` for the complete source-file list relative to
this worktree. Main groups:

- `app/entitlements.py`,config/models,workspace IO/export/receipt/body-limit
  helpers and private workspace API routers.
- Additive migrations 0024–0029; tests for each phase, security, migration
  preservation, formats, analytics and PostgreSQL races.
- Shared header/sidebar/tool grid, homepage/pricing/solutions, private tool pages
  and browser scripts/workers, nine public landing pages and sitemap/robots.
- Admin usage/funnel reporting, privacy description and regression/build checks.
- Dependency manifests/locks and the audit/report. Local fixtures, logs,
  synthetic credentials and screenshots under`.local/` are not committed.

## Remaining operational checks and limitations

No unfinished tool is advertised as available. Team workspaces remain disabled
as a future feature. Bulk jobs are intentionally tab-local, not durable background
jobs. OnlySVG image batches are produced by Bulk Studio; existing generation
endpoints still supportPNG. Audit scores measure documented completeness checks,
not real-world accuracy or GS 1 registration. Physical print quality still needs
an actual-size test on the intended printer/stock. No live payment, production
latency/load test or customer-data migration was performed. Current dependency
warnings are Starlette TestClient/httpx and Python SQLite datetime-adapter
upstream deprecations; they did not fail tests. A verified production product
count is still unavailable until the approved operator refresh succeeds.

## Deployment boundary and cost

Do not push to main or deploy automatically: Render auto-deploys that branch.
After explicit release approval:

1. Take and verify a production backup; review migrations 0024–0029 and the
   existing-customer policy before scheduling release.
2. Install locked dependencies and apply `alembic upgrade head` to staging. Test
   a legacy account and all four plan roles. The existing Docker CMD also runs
   migrations before Uvicorn, so the release image will migrate its selected DB.
3. Deploy the API before the website. Verify health, session cookies, allowed
   origins, premium/owner checks and a synthetic barcode/label workflow.
4. Populate the verified count via
`python -m app.tools.refresh_statistics --timeout-seconds 120`, then build and
deploy the static website/Worker with `PUBLIC_API_ORIGIN` set to the intended API.
5. Recheck sitemap, mobile navigation, account access and Stripe test-mode
   signature/idempotency flows. Monitor process memory, latency and error rates.
   Keep production secrets unchanged.

Prefer an application rollback that retains the new migration files and additive
schema. Downgrades remove new tables/columns and their newly created data; back
that data up before any schema rollback. The preservation tests cover rows that
existed before this expansion. The refresh uses the selected environment's database;
do not run it against production without approval. No new paid service is needed. Python adds openpyxl/defusedxml (plus et-xmlfile);
the frontend adds BWIP and fflate. Storage grows with private records/history and
receipts. Existing Render/Neon/Cloudflare infrastructure remains in use.
Snapshot reads add negligible query work; the exact refresh is an explicitly
bounded operator operation rather than repeated visitor work.

Modified files can be reviewed with `git diff 1e65147 --stat` and the local
phase commits. Audit details and schema dependencies are recorded in
`PLATFORM_EXPANSION_AUDIT.md`.
