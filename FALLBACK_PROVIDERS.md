# Fallback provider policy and operations

Checked against the official documentation on 2026-08-19. Provider terms and
limits can change; review them before enabling fallback in production.

## Routing and storage decisions

| Provider | Official API | Authentication | Published limit | Storage/licensing position | BarcodeNest decision |
|---|---|---|---|---|---|
| Open Facts (Food, Beauty, Pet Food, Products) | `https://world.openfoodfacts.org/api/v3/product/{code}?product_type=all` | Identifying `User-Agent`; reads need no account | Product reads: 15 requests/minute/IP | Database: ODbL; individual contents: DbCL; images: CC BY-SA, with possible underlying rights | Query first for non-books. Persist normalized product and provenance. Retain license metadata and source URL. One v3 `product_type=all` call avoids four blind requests. |
| UPCitemdb | `https://api.upcitemdb.com/prod/trial/lookup` or `/prod/v1/lookup` | Free trial: none; paid: `user_key` and `key_type` headers | Free: 100 combined/day, 6 lookups/minute, sustainable 1/10 seconds. Paid limits depend on plan. | Public terms alone are ambiguous. The BarcodeNest operator confirmed on 2026-08-20 that UPCitemdb separately granted written permission to store returned product data. The written grant must be retained outside this repository and its scope reviewed before redistribution changes. | Miss-only, after Open Facts (or book sources). Validate and persist normalized fields plus minimal internal provenance when `UPCITEMDB_PERSISTENCE_ENABLED=true`. No callback or additional request is sent to UPCitemdb when BarcodeNest saves locally. |
| Google Books | `https://www.googleapis.com/books/v1/volumes?q=isbn:{ISBN}` | API key for public-data identification; OAuth for private data | Quotas are project-controlled in Google Cloud; no fixed universal allowance documented on the Books usage page | Google API Terms prohibit permanent copies/database building and caching longer than response cache headers unless separately permitted. Google Books also requires attribution and prominent Google Books links when results are displayed. | ISBN-only, first book source for completeness. Return current-request metadata only; no product or positive-response persistence. Negative check state contains no Google content. Production UI/API attribution needs legal/product review before enabling. |
| Open Library | `https://openlibrary.org/api/books?bibkeys=ISBN:{ISBN}&jscmd=data&format=json` | None; identifying `User-Agent` with contact requested | Default 1 request/sec; identified requests 3/sec | Internet Archive asserts no new proprietary rights over database material; contributions are requested under CC0, but third-party rights may remain. APIs are for low-volume, human-facing discovery and not high-traffic commercial backend use. Covers may have separate rights. | ISBN-only after Google Books. Persist low-volume validated catalog metadata with provenance. Keep source links; do not download/rehost covers. Contact Open Library before meaningful commercial traffic or use a licensed bulk strategy. |

Official references:

- Open Facts API, licenses, limits and identification: <https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/>
- Open Facts product endpoint: <https://openfoodfacts.github.io/documentation/docs/Product-Opener/v3/products/get-api-v3-product-code/>
- UPCitemdb API and free plan: <https://www.upcitemdb.com/api/>
- UPCitemdb plan limits: <https://www.upcitemdb.com/wp/docs/main/development/plan/>
- UPCitemdb response errors: <https://www.upcitemdb.com/wp/docs/main/development/responses/>
- Google Books usage: <https://developers.google.com/books/docs/v1/using>
- Google API Terms, especially content caching: <https://developers.google.com/terms>
- Google Books branding: <https://developers.google.com/books/branding>
- Open Library API guidelines and limits: <https://openlibrary.org/developers/api>
- Open Library licensing: <https://openlibrary.org/developers/licensing>
- Open Library data-use FAQ: <https://openlibrary.org/help/faq/using>

## Negative cache and failure behavior

`fallback_provider_states` stores only GTIN, provider, check status, timestamps,
and a bounded diagnostic category. It stores no API keys and no restricted
provider response content. Default negative TTL is 24 hours for each provider.
429/temporary provider failures use `Retry-After` where present (otherwise a
short backoff), so repeated customer misses do not consume external quotas.
Expired entries are eligible for checking again because public catalogs change.

Each network call defaults to a 2.5-second timeout. Timeouts, DNS errors, 401,
403, 429, 5xx, malformed JSON, wrong identifiers, and incomplete records are
isolated and never exposed as provider errors to customers. UPCitemdb also has
a process-local 10-second minimum interval matching its published sustainable
free-tier rate. This limiter is not distributed; use one application replica or
add a shared limiter before scaling.

## Metrics

Lookup analytics distinguish local hits from recovered hits and expose:

- total valid lookups
- local hits and local misses
- fallback attempts and fallback hits
- final misses
- local hit rate
- fallback recovery rate
- effective hit rate

The customer usage counter is updated once by the API route after resolution;
provider attempts never increment customer usage.
