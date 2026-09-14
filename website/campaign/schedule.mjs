// All publication instants use UTC. No client clock or daily rebuild is involved.
export const start = '2026-09-14T08:05:00Z';
export const entries = [
  ['best-barcode-lookup-apis-2026', 'Best Barcode Lookup APIs for Developers in 2026', 'best barcode lookup APIs'],
  ['upc-lookup-api', 'UPC Lookup API: How to Get Product Data From a UPC', 'UPC lookup API'],
  ['ean-lookup-api', 'EAN Lookup API: Search Product Information by EAN-13', 'EAN lookup API'],
  ['free-barcode-lookup-api', 'Free Barcode Lookup API: What Developers Need to Know', 'free barcode lookup API'],
  ['gtin-lookup-api', 'GTIN Lookup API: GTIN-8, GTIN-12, GTIN-13 and GTIN-14 Explained', 'GTIN lookup API'],
  ['build-barcode-scanner-app-api', 'How to Build a Barcode Scanner App With a Product Lookup API', 'barcode scanner app API'],
  ['barcode-api-pricing', 'Barcode API Pricing: Comparing Free and Paid Barcode Lookup APIs', 'barcode API pricing'],
  ['upc-vs-ean-vs-gtin', "UPC vs EAN vs GTIN: What's the Difference?", 'UPC vs EAN'],
  ['get-product-data-from-barcode', 'How to Get Product Name, Brand and Images From a Barcode', 'get product information from barcode'],
  ['barcode-database-for-developers', 'Barcode Database for Developers: Choosing a Product Data Source', 'barcode database'],
].map(([slug, title, keyword], index) => ({slug, title, keyword,
  published: new Date(Date.parse(start) + index * 86400000).toISOString()}));
export function publishedEntries(now = Date.now()) {
  return entries.filter(entry => Date.parse(entry.published) <= now);
}
export function entryForPath(path) {
  let decoded;
  try { decoded = decodeURIComponent(path); } catch { return undefined; }
  const slug = decoded.replace(/\/{2,}/g, '/').replace(/\/index\.html$/, '/').replace(/\.html$/, '').split('/').filter(Boolean);
  return slug[0] === 'blog' ? entries.find(entry => entry.slug === slug[1]) : undefined;
}
