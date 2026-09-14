# September 2026 developer campaign

Ten articles are ordered in `schedule.mjs`. Publication starts September 14,
2026 at 08:05 UTC and advances by exactly 86,400 seconds through September 23.
UTC is used for Cloudflare's server clock, structured data, sitemap and visible
dates; the reader's device clock and daylight-saving time do not affect access.

## Architecture and deployment

The existing production target is Cloudflare Workers Builds, service `v1`,
connected to `VishuPS/V1` branch `main`. Preserve its existing build/deploy
configuration. `pnpm build` runs Astro then packages the ten rendered pages
into `generated/campaign.mjs`, a Worker-only import. The corresponding HTML
files are removed from `dist` before Wrangler uploads assets. Never deploy
the intermediate output of `astro build` without the packaging step.

The Worker runs before static assets, using the same date predicate for routes,
index cards, structured listing data, sitemap and RSS. Future routes return
uncached 404 with `X-Robots-Tag: noindex`. Common `.html`, `index.html`, encoded
and slash variants cannot reveal the article. Published aliases redirect to
the trailing-slash canonical URL. No preview query parameter or client clock
override exists in production. No daily rebuild, cron, database or secret is
required. Publication becomes visible on the first request at/after its instant.

Campaign pages, the index, sitemap and feed use `Cache-Control: no-store` so
cached negative responses or old listings do not delay the next day's release.
Links to future campaign articles are rendered as ordinary text until the
target is public. Existing articles and contributor-profile routes are preserved.
There is no internal search in the current site. A campaign RSS feed is added
at `/blog/rss.xml`; it includes only published campaign items. Three existing
DPP posts missing from the static sitemap have also been included.

Content is an Astro schema-validated collection in `src/content/campaign`.
The existing BlogPostLayout, typography, header, footer and CTA styles are reused.
Each article has unique metadata, Article JSON-LD, a table of contents, cited
sources, FAQs, examples, contextual CTA and signup/documentation links.
FAQ rich-result markup is intentionally not added. The site does not currently
use breadcrumb structured data. Article schema is emitted only on public routes.

## Validation

Run `pnpm check`, `pnpm build`, `pnpm test:campaign`, `pnpm check:seo`.
The existing Python suite is run with `python -m pytest` from the repository root.
CI now runs the website checks as well as the existing backend/image job.
`scripts/preview-campaign.mjs` is a localhost-only review harness simulating all
publication dates; it is not bundled in production. `scripts/review-campaign.cjs`
checks all articles at 375, 768 and 1440 pixels with Playwright and headless Edge.
`scripts/verify-production.py` makes public, unauthenticated deployment checks.

## Editorial fact check

Technical source review: September 13, 2026. BarcodeNest contracts were checked
against `app/barcodes.py`, `app/schemas.py`, `app/api/routes.py`, authentication
and billing configuration, plus published pricing and endpoint documentation.
Examples use GET `/v1/products/{barcode}` and `X-API-Key`; no live key is embedded.
The UPC response is explicitly an illustrative schema example, not a fabricated
live result. Found products can have optional/missing metadata. The product
schema has no dedicated manufacturer, description or live price field.

Commercial tables are explicitly September 13 snapshots rather than promises
about later publication dates. Provider figures should be rechecked before a
purchasing decision or a future editorial refresh. No universal coverage,
database-size ranking, measured uptime, customer counts or unsupported superiority
claims are made. No paid provider is ranked first by default.

Primary sources:

- https://barcodenest.com/pricing/ — Free 250/month, 30/min; Starter $9.99,
  2,000/month, 300/min; Growth $19.99, 5,000/month, 1,200/min. Annual billed
  totals $95.90/$191.90. Allowances remain monthly.
- https://www.barcodelookup.com/api — Starter $99/month, 5,000 calls;
  test account available; overage terms not asserted.
- https://devs.upcitemdb.com/ — Explorer 100 combined/day, DEV $99/month,
  20,000 lookup/day and displayed 600,000/month; lookup overage $0.04/100,
  with daily-limit extension conditions. Search allowances are separate.
- https://openfoodfacts.github.io/openfoodfacts-server/api/ — open food data,
  API usage conditions and licensing; no unlimited entitlement asserted.
- https://www.gs1.org/standards/id-keys/gtin — identifier framework.
- https://www.gs1.org/services/how-calculate-check-digit-manually — check digits.
- https://www.gs1.org/docs/barcodes/GSCN-22-169-GTIN8NotForGroupings.pdf —
  packaging indicators and recalculated check digits.
- https://www.isbn-international.org/content/isbn-users-manual/29 — ISBN context.
- https://developer.mozilla.org/en-US/docs/Web/API/BarcodeDetector — limited
  browser availability, format detection and secure contexts.
- https://github.com/zxing-js/browser — documented browser decoding alternative.

API examples were checked against repository routes/schema and validation
behavior. A production authenticated example requires a user-owned API key;
public availability checks must not create accounts or disclose credentials.
