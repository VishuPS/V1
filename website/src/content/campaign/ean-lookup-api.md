---
description: "Look up EAN-13 product information with BarcodeNest. Learn check-digit validation, UPC compatibility, normalization and safe Python and JavaScript integration."
keywords: "EAN lookup API, EAN API, EAN-13 product lookup, GTIN-13"
---
An EAN lookup API resolves a product identifier against a database and returns available metadata. For EAN-13, submit the complete thirteen-digit string, including its check digit. BarcodeNest accepts that value at `GET /v1/products/{barcode}` with an API key in the `X-API-Key` header.

EAN-13 appears on products across international retail markets, including Europe. It identifies a trade item; it does not encode the product's name, ingredients or photograph. Your application obtains those details from the lookup response, subject to source coverage and field completeness.

## EAN-13 structure and terminology

EAN-13 is a barcode symbology carrying a GTIN-13. GTIN is the identifier framework, while EAN describes the familiar machine-readable representation. The last digit is a check digit calculated from the preceding twelve digits. The allocation structure includes a GS1 Company Prefix and item reference; their boundaries should not be guessed from a fixed number of digits.

Refer to [GS1's GTIN documentation](https://www.gs1.org/standards/id-keys/gtin) for the formal identifier system. Do not infer a product's manufacturing country from an apparent prefix. Assignment context and manufacturing location are different facts; use verified product or supplier data for origin information.

Keep scanner output as text. Leading zeros are especially important when UPC-A products are represented as thirteen digits. Your browser, server, queue and database should preserve exactly what was scanned before deriving a canonical key.

## Validate the check digit

An EAN-13 string needs thirteen ASCII digits and a matching check digit. Starting from the rightmost body digit, alternate weights of three and one. Subtract the sum's remainder modulo ten from ten, using zero when the remainder is zero. See the [GS1 manual calculation guide](https://www.gs1.org/services/how-calculate-check-digit-manually).

For `3017620422003`, the body is `301762042200` and the check digit is `3`. Validation can catch many entry mistakes, but it cannot establish assignment, authenticity or database availability. Do not describe a barcode as an authentic product just because the arithmetic works.

```javascript
export function validEan13(value) {
  if (typeof value !== "string" || !/^[0-9]{13}$/.test(value)) return false;
  const sum = [...value.slice(0, -1)].reverse().reduce(
    (total, digit, i) => total + Number(digit) * (i % 2 === 0 ? 3 : 1), 0
  );
  return (10 - sum % 10) % 10 === Number(value.at(-1));
}
```

Preserve invalid input for a user correction workflow only where appropriate; do not forward it repeatedly to a paid service. A clear message such as “Check the thirteen digits below the barcode” is more useful than a generic failed lookup alert.

## Send an authenticated lookup

This curl example uses the same EAN as the BarcodeNest documentation. Set `BARCODENEST_API_KEY` in your shell environment after obtaining a key through [registration](/register/). Product coverage can change, so treat the command as a real request rather than a promise of particular field values.

```bash
curl --fail-with-body --max-time 15 \
  "https://api.barcodenest.com/v1/products/3017620422003" \
  -H "X-API-Key: $BARCODENEST_API_KEY"
```

A successful response has `barcode`, `barcode_type`, `canonical_gtin`, `valid`, `found`, `product`, `source` and `error` fields. For this identifier, the canonical value is `03017620422003`. The actual product name, image and other values depend on the indexed record.

The `product` schema includes `name`, `brand`, `categories`, `quantity`, `image_url`, `ingredients`, `allergens`, `nutrition` and `countries`. Some values may be null or empty. `source` identifies the source using `name` and `source_id`. These are the fields to model; do not add an imaginary price or manufacturer field to sample code.

<div class="article-callout"><span>Try the lookup flow</span><p><a href="/register/">Get a free BarcodeNest API key</a> and test an EAN from your own packaging. The <a href="/docs/barcode-lookup/">API reference</a> documents successful responses and batch behavior.</p></div>

## Python integration with explicit failure states

The following standard-library function validates EAN-13 and sends an authenticated request. A 404 returns `None`; other HTTP errors remain errors for the caller to handle. Network exceptions are also allowed to propagate, keeping them distinct from missing catalog data.

```python
import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

def lookup_ean(ean: str):
    if not isinstance(ean, str) or not re.fullmatch(r"[0-9]{13}", ean):
        raise ValueError("Expected thirteen ASCII digits")
    total = sum(int(d) * (3 if i % 2 == 0 else 1)
                for i, d in enumerate(reversed(ean[:-1])))
    if (10 - total % 10) % 10 != int(ean[-1]):
        raise ValueError("Invalid EAN-13 check digit")
    request = Request(
        f"https://api.barcodenest.com/v1/products/{ean}",
        headers={"X-API-Key": os.environ["BARCODENEST_API_KEY"]},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
```

An import job should record a result for each input without abandoning the whole file on one missing product. Keep validation errors, unresolved identifiers and service failures in separate categories. This makes it possible to correct the input, review catalog gaps or retry temporary failures appropriately.

## JavaScript integration on your server

Use a runtime with built-in `fetch` and `AbortSignal.timeout`. The API key below comes from the server environment. Do not copy this credential-handling pattern into browser code or expose the environment value through a public build variable.

```javascript
export async function lookupEan(ean) {
  if (!validEan13(ean)) throw new Error("Invalid EAN-13");
  const key = process.env.BARCODENEST_API_KEY;
  if (!key) throw new Error("Server API key is not configured");
  const response = await fetch(
    `https://api.barcodenest.com/v1/products/${encodeURIComponent(ean)}`,
    { headers: { "X-API-Key": key }, signal: AbortSignal.timeout(15000) }
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Lookup failed: HTTP ${response.status}`);
  const data = await response.json();
  return { id: data.canonical_gtin, product: data.product, source: data.source };
}
```

The final object is an application projection, not a different BarcodeNest response schema. Keeping that distinction visible helps teammates debug where transformations occur. Retain the original response separately when your audit and data-use requirements call for it.

## Normalize equivalent UPC and EAN values

A twelve-digit UPC-A identifier may appear with a leading zero as a thirteen-digit representation. Both can resolve to the same fourteen-digit canonical GTIN. Use that canonical string for cache keys and duplicate detection after validation, while retaining the raw scanned value for troubleshooting.

Do not strip arbitrary leading digits. A nonzero GTIN-14 indicator can represent a different packaging level. A case is not the same trade item as the single unit it contains. Normalization aligns equivalent representations; it does not collapse packaging hierarchies. The [GTIN guide](/blog/gtin-lookup-api/) explains this distinction in more detail.

For systems combining American and international supplier feeds, establish one normalization policy at the ingestion boundary. Otherwise the same product may appear under separate UPC and EAN keys in inventory, analytics and caching. Our [UPC API tutorial](/blog/upc-lookup-api/) covers the twelve-digit workflow.

## Missing products, incomplete records and limits

An unknown EAN can be perfectly valid. Source coverage varies with region, private labels, release timing and product category. Show the scanned identifier and offer manual entry or a later retry. Do not replace the item with a similarly named product: that can attach incorrect quantity or ingredient information.

A found product may also be incomplete. Display a neutral image placeholder when `image_url` is absent. Leave unsupported attributes out of the UI instead of filling them with assumptions. An empty allergen array must not be converted into a safety claim that a product is allergen-free.

For HTTP 429, read the [rate-limit documentation](/docs/rate-limits/) and distinguish a short-window throttle from exhausted monthly usage. Deduplicate repeated scans and cache permitted results. Set a shorter cache duration for misses, and never store a network failure as a permanent negative result.

## FAQ

### Is EAN-13 the same as GTIN-13?

GTIN-13 is the identifier; EAN-13 is the barcode representation that carries it. Developers often use “EAN” informally for the digits, but the distinction matters when a scanner reports both format and value.

### Can an EAN lookup return a product image?

BarcodeNest supports `product.image_url`, but a usable image is not guaranteed for every record. Check both field presence and image loading behavior.

### Does an EAN prefix reveal where an item was manufactured?

Do not use it as proof of manufacturing origin. Use explicit, verified origin data from a relevant source.

### Should my application retry a 404 immediately?

Usually no. Repeating the same valid identifier immediately is unlikely to add missing data. Record the unresolved state, use a bounded negative-cache period and offer correction or contribution.

## Connect lookup to the product experience

Make the validated identifier the link between the scan and your product record. Preserve source details, handle incomplete metadata and protect the API key behind your application server. For the broader identity model, see our [UPC, EAN and GTIN comparison](/blog/upc-vs-ean-vs-gtin/) and the existing [GS1 Digital Link guide](/blog/gs1-digital-link-explained/).
