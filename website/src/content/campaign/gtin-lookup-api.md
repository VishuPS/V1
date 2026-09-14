---
description: "Understand GTIN-8, GTIN-12, GTIN-13 and GTIN-14, validate check digits and normalize identifiers before querying the BarcodeNest product lookup API."
keywords: "GTIN lookup API, GTIN API, GTIN product lookup, GTIN normalization"
---
A GTIN lookup API accepts a Global Trade Item Number and searches product data associated with that identifier. BarcodeNest supports valid eight-, twelve-, thirteen- and fourteen-digit inputs, returning a canonical fourteen-digit string together with any matching product record.

The main implementation rule is simple: validate first, normalize second, and preserve identity throughout. Adding leading zeros for storage can preserve the same identifier; changing packaging indicators or discarding arbitrary digits can identify a different trade item. A robust application treats those operations very differently.

## GTIN is an identifier framework

GS1 defines GTIN as a key for identifying trade items. It is not the name of one particular set of printed bars, and it does not contain a complete product record. A lookup service connects the key to descriptive data maintained elsewhere. The [GS1 GTIN overview](https://www.gs1.org/standards/id-keys/gtin) is the primary reference.

Developers commonly receive a GTIN as scanner text, supplier data or a spreadsheet column. Its format may vary with the source system. The application needs to recognize supported representations without assuming that every numeric string is a valid retail identifier.

| Identifier | Digits | Common relationship | Developer concern |
| --- | --- | --- | --- |
| GTIN-8 | 8 | Carried by EAN-8 | Not interchangeable with raw UPC-E output |
| GTIN-12 | 12 | Carried by UPC-A | Preserve leading zeros |
| GTIN-13 | 13 | Carried by EAN-13 | Do not infer fixed company-prefix boundaries |
| GTIN-14 | 14 | Used for trade items including packaging groupings | A nonzero indicator may distinguish a different trade item |

The number of digits tells you which structure to validate, but not the product category or source completeness. BarcodeNest names these accepted input forms `EAN-8`, `UPC-A`, `EAN-13` and `GTIN-14` in `barcode_type`. Keep the distinction between that reported input type and the normalized key.

## Check digits detect input errors

All four supported lengths use a final check digit. Remove that digit, walk the remaining body from right to left with alternating weights of three and one, sum the products, and calculate `(10 - sum % 10) % 10`. Compare the result with the supplied final digit. [GS1 documents the calculation](https://www.gs1.org/services/how-calculate-check-digit-manually).

```javascript
export function canonicalGtin(input) {
  if (typeof input !== "string") throw new Error("GTIN must be a string");
  const value = input.trim();
  if (!/^(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})$/.test(value)) {
    throw new Error("Unsupported GTIN format");
  }
  const total = [...value.slice(0, -1)].reverse().reduce(
    (sum, digit, index) => sum + Number(digit) * (index % 2 === 0 ? 3 : 1), 0
  );
  if ((10 - total % 10) % 10 !== Number(value.at(-1))) {
    throw new Error("Invalid check digit");
  }
  return value.padStart(14, "0");
}
```

This helper validates form, not assignment or authenticity. An identifier can pass arithmetic and still not correspond to a known trade item. It can also be correctly assigned yet missing from a particular database. Your application should not collapse those concepts into one “verified product” flag.

## Zero padding preserves equivalent representations

Consider these strings:

```text
UPC-A / GTIN-12:        036000291452
13-digit representation: 0036000291452
14-digit storage:        00036000291452
```

Leading-zero padding aligns them with a fourteen-digit storage field. It does not allocate a new GTIN or alter the underlying product identity. For validated shorter GTINs, added zeros do not change the weighted check-digit total.

The reverse operation needs care. Do not use a universal “remove all leading zeros” function and then infer the type from what remains. That can leave an unsupported number of digits and erase the representation supplied by a source. Store both the original string and canonical string when you need round-tripping or audit history.

<div class="article-callout"><span>Inspect canonical identifiers</span><p><a href="/register/">Create a free BarcodeNest API key</a> and inspect <code>canonical_gtin</code> in a real response. The <a href="/docs/barcode-lookup/">endpoint guide</a> lists accepted input lengths.</p></div>

## GTIN-14 packaging is not a padding shortcut

A fourteen-digit code beginning with a nonzero indicator can identify a packaging grouping rather than the individual unit. Removing the indicator does not safely produce the unit's barcode. The check digit may also differ because it was calculated for the full identifier.

GS1's [packaging grouping guidance](https://www.gs1.org/docs/barcodes/GSCN-22-169-GTIN8NotForGroupings.pdf) explains the role of indicator values and recalculated check digits. In a warehouse application, model relationships such as “case contains units” explicitly. Do not create that relationship by slicing a string and assuming the result is an interchangeable key.

The practical risk is quantity corruption. If a receiving system collapses a case and a unit into one identity, it can record incorrect stock counts even when the name looks familiar. Match packaging level, quantity and variant using trustworthy product or supplier data.

## Make a BarcodeNest GTIN request

Use the same endpoint for every supported length. This example submits a fourteen-digit representation of the documented EAN sample. Set the API key in your server or shell environment first.

```bash
curl --fail-with-body --max-time 15 \
  "https://api.barcodenest.com/v1/products/03017620422003" \
  -H "X-API-Key: $BARCODENEST_API_KEY"
```

A found response includes `barcode`, `barcode_type`, `canonical_gtin`, `valid`, `found`, `product`, `source` and `error`. The product object contains supported descriptive attributes where available. A valid but unresolved single lookup returns HTTP 404 rather than an invented product object.

Use the canonical key to deduplicate permitted cached results. Keep the returned source details and your retrieval timestamp. Neither the presence of a canonical key nor a successful lookup certifies the physical item. Read the [error reference](/docs/errors/) and [rate-limit guide](/docs/rate-limits/) before turning a shell test into a production service.

## Design storage for identifiers

Use a string field with validation instead of an integer field. Numeric storage loses leading zeros and encourages inappropriate arithmetic. A database `VARCHAR(14)` or equivalent can hold accepted forms; a canonical field can enforce exactly fourteen ASCII digits after your validation layer.

For each source observation, retain the submitted identifier, canonical identifier, source name, source record identifier and retrieval time. Keep your application-specific SKU separately. A merchant SKU belongs to a merchant's catalog; it is not automatically a GTIN and should not be sent to a GTIN endpoint merely because it consists of digits.

Decide where uniqueness belongs. Several source observations may refer to one canonical trade item. A uniqueness constraint on the observation table can accidentally prevent you from retaining provenance. A separate canonical product table and source-record table often make that relationship clearer.

## Common normalization mistakes

**Accepting any digits.** A length check without check-digit validation lets typing errors become cache keys and product records. Validate at ingestion, while still treating the API as the authority for its own input rules.

**Confusing UPC-E and EAN-8.** Both can be encountered as eight characters in some scanner output conventions. Preserve the decoder's symbology and expand UPC-E correctly before treating it as UPC-A. Do not run it through EAN-8 validation as a guess.

**Discarding packaging distinctions.** A case and single unit can share similar names but require different identifiers. Preserve their relationship instead of forcing equality.

**Treating all misses as malformed input.** A correct GTIN may simply not be indexed. Store a separate unresolved state and allow later enrichment.

**Using local SKU fallback as GTIN truth.** If a lookup fails and a user supplies an internal stock code, do not put that value into a canonical GTIN column. Model alternate identifiers with their own namespaces.

## FAQ

### Does BarcodeNest accept all four GTIN lengths?

Its documented lookup endpoint accepts valid eight-, twelve-, thirteen- and fourteen-digit inputs. Format support does not guarantee a matching product for every valid value.

### Is padding a GTIN-12 to fourteen digits a new barcode assignment?

No. It is a storage representation of the same identifier. Assigning identifiers and defining packaging levels are separate processes governed by the applicable standards.

### Can a valid GTIN tell me the product name without a database?

No. The identifier is a lookup key. Product names and other descriptive values come from associated data sources.

### Which key should my cache use?

Use a validated canonical GTIN where representations are equivalent, and keep packaging variants separate. Retain source and retrieval metadata according to your application and data-use requirements.

## Keep identity stable

A good GTIN integration preserves strings, validates check digits and normalizes only equivalent forms. That foundation supports reliable caching, imports and user interfaces. Continue with the [UPC tutorial](/blog/upc-lookup-api/), [EAN tutorial](/blog/ean-lookup-api/) or [identifier comparison](/blog/upc-vs-ean-vs-gtin/) for related workflows. Our existing [Digital Link guide](/blog/gs1-digital-link-explained/) shows how identifiers also appear in web-based product links.
