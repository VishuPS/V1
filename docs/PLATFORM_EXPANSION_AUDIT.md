# Platform expansion audit — 25 November 2026

## Baseline and release boundary

Baseline: `1e65147`. Implementation branch: `codex/platform-expansion`.
Do not push to main, run Wrangler deployment, change Render variables, apply
production migrations, or modify live Stripe configuration as part of this work.
Render's main-branch automatic deployment is enabled. All implementation and
migration tests must use isolated local databases.

## Existing functionality

The repository contains 208 application, migration and frontend source files.
FastAPI/SQLAlchemy/Pydantic serve the API; PostgreSQL is the production database,
SQLite supports isolated tests, and Alembic manages schema changes. Astro builds
the website; a Cloudflare Worker handles assets and scheduled blog publication.
The existing cream/ink/lime CSS is the live brand system. An older design-system
document describes a different palette and must not override the user's explicit
instruction to retain the current identity.

Implemented tools: single/batch product lookup; UPC/EAN/Code 128/QR SVG and PNG
generation; image decoding; account validation; fixed A4/Letter PDF labels;
CSV enrichment; and the private product Vault. Reuse these services. Vault
already supports product creation/editing, collections, notes, prices, custom
fields, CSV/JSON export and label selection. It is the private catalogue base,
not a second public product database.

There are no SKU sequences, inventory ledger, reusable label templates,
asset register, general bulk job workspace, or catalogue auditor yet. These must
not be advertised as available until they have working UI, API and tests.

## Authentication and customer isolation

Account sessions use short-lived JWTs, refresh-token hashes, secure cookies in
production and Argon2 passwords. Google/GitHub OAuth and email verification
already exist. API-key digests authenticate integrations. Website tools should
use account-session wrappers; CSV labels/enrichment currently ask for an API key
and need account wrappers instead. Every new resource must have an owner ID and
all read/write queries must constrain that ID. Origin checks protect cookie
mutations; administrative endpoints retain their existing role checks.

Vault uniqueness uses an owner-scoped canonical identifier digest. PostgreSQL
user-row locks protect capacity checks. Revision checks prevent stale edits.
Reuse these patterns for SKU allocation, stock movements, templates and assets.

## Schema and production evidence

A read-only production audit confirmed Alembic head `20261124_0023` and the
existing public-product, source, customer, subscription, session, quota,
contribution, recovery, webhook and Vault tables. The subscription audit found
150 Free accounts and one Starter account; no customer identities were collected
for this document. Production hosting is Render Starter in Oregon, with the
database on Neon in Frankfurt and static assets on Cloudflare.

An exact product count exceeded a 15-second statement timeout. Therefore the
homepage must not execute a full COUNT on each visitor request or repeat the
hard-coded 4.35M claim. Publish a verified statistics snapshot with its timestamp,
refreshed by a bounded operator/ingestion task. If absent or unavailable, show an
honest unavailable state. Never substitute PostgreSQL planner estimates as exact
verified counts.

## Billing and entitlement baseline

Existing plans: Free, One-time lookup packs, Creator $4.99/month, Starter
$9.99/month or $95.90/year, Growth $19.99/month or $191.90/year. Annual prices
are recurring; legacy incorrect annual links were retired. Preserve all live
Stripe IDs and prices. Webhook signature verification, event idempotency,
monthly usage under annual subscriptions, and portal plan changes already have
tests and must remain intact.

Product lookup: Free 250 lifetime successful-result credits; Creator zero;
Starter 2,000/month; Growth 5,000/month. Existing one-time pack rules remain
unchanged. Tool allowances are separate monthly account counters. Existing Vault
capacity: Free/One-time 25, Creator 250, Starter 2,000, Growth 10,000.

The proposed 500 Creator records apply to new customers. Add a versioned
workspace policy to distinguish existing accounts from new ones; do not mutate
old subscriptions or silently change their capacity. Central server entitlements
must replace scattered paid-plan checks while preserving integration quotas.
Expired premium resources remain readable; never delete records on expiry.

## Migration and implementation dependencies

1. Phase 1: additive policy-version column, public statistics snapshot table;
   central entitlement service and session endpoint; shared tool registry,
   navigation, workspace and homepage. Existing accounts receive legacy policy.
2. Phase 2: SKU sequences and owner-scoped allocations; private catalogue
   identifiers/variants/suppliers; label templates and validated image assets.
   Reuse Vault ownership, revisions and barcode renderers. Add bounded XLSX
   import/export without macros, formulas or external-resource evaluation.
3. Phase 3: inventory balances and immutable stock movement ledger, atomic
   updates/idempotency; bounded bulk processing and retry reports; documented
   catalogue quality rules and correction links.
4. Phase 4: asset records, maintenance and movement ledger; implemented-tool
   landing pages; allowlisted analytics events without product content, keys,
   filenames, free-form text or PII.
5. Phase 5: regression, owner-isolation, premium-access, concurrency, migration,
   decoding/geometry, file-validation, accessibility and responsive tests.

All migrations must be reversible and preserve existing rows during upgrade.
Stock must never be updated by an unguarded read/modify/write cycle. No mixed-
currency valuation total may be presented without conversion semantics.

## Cost and operational constraints

Retain the existing single API container and static site. Avoid new paid
services, a separate queue system or unbounded in-memory jobs. Put suitable CSV
validation/generation work in browser workers; keep server batches bounded and
account quotas authoritative. Limit uploaded archive size, decompressed cells,
rows, image pixels and template complexity. Use pagination and indexed
owner-scoped queries. Public statistics are cheap snapshot reads. Large imports
must report partial outcomes, cancellation and safe retry semantics explicitly.

## Validation baseline and report requirements

The last deployed baseline passed 393 API tests and existing frontend/SEO
checks. Record phase-specific tests and final results in
`PLATFORM_EXPANSION_REPORT.md`, along with changed files, migration instructions,
costs, security limitations and remaining work. Do not label a phase complete
without passing its relevant acceptance criteria.
