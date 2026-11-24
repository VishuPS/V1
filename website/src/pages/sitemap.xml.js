import { staticPosts } from "../content/blog-posts.mjs";
const pages = [
  ["blog/scan-barcode-from-image/", "0.8", "monthly"],
  ["blog/create-barcode-svg-png/", "0.8", "monthly"],
  ["blog/print-barcode-labels-from-csv/", "0.8", "monthly"],
  ["barcode-scanner/", "0.8", "monthly"],
  ["barcode-generator/", "0.8", "monthly"],
  ["barcode-labels/", "0.8", "monthly"],
  ["csv-product-enrichment/", "0.8", "monthly"],
  ["docs/barcode-tools/", "0.8", "monthly"],
  ["blog/enrich-barcode-csv-python/", "0.8", "monthly"],
  ["blog/barcode-lookup-php/", "0.8", "monthly"],

  ["blog/retail-unit-vs-case-barcode/", "0.8", "monthly"],
  ["blog/can-a-barcode-prove-a-product-is-real/", "0.8", "monthly"],
  ["blog/sku-vs-upc-inventory/", "0.8", "monthly"],
  ["blog/batch-barcode-lookup-api/", "0.8", "monthly"],
  ["blog/keep-leading-zeros-barcodes-excel/", "0.8", "monthly"],
  ["blog/barcode-check-digit-javascript/", "0.8", "monthly"],
  ["barcode-validator/", "0.8", "monthly"],
  ["products/", "0.8", "weekly"],
  ["", "1.0", "weekly"],
  ["barcode-api/", "0.9", "monthly"],
  ["upc-api/", "0.9", "monthly"],
  ["ean-api/", "0.9", "monthly"],
  ["gtin-api/", "0.9", "monthly"],
  ["barcode-database/", "0.9", "monthly"],
  ["docs/", "0.9", "weekly"],
  ["docs/barcode-lookup/", "0.8", "monthly"],
  ["docs/authentication/", "0.8", "monthly"],
  ["docs/rate-limits/", "0.8", "monthly"],
  ["docs/errors/", "0.8", "monthly"],
  ["barcode-api-for-inventory/", "0.8", "monthly"],
  ["product-data-api/", "0.8", "monthly"],
  ["pricing/", "0.9", "monthly"],
  ["data/", "0.7", "monthly"],
  ["blog/", "0.8", "weekly"],
  ["blog/barcode-lookup-javascript/", "0.8", "monthly"],
  ["blog/barcode-lookup-not-found/", "0.8", "monthly"],
  ["blog/barcode-lookup-python/", "0.8", "monthly"],
  ["blog/2d-barcodes-2027/", "0.8", "monthly"],
  ["blog/gs1-digital-link-explained/", "0.8", "monthly"],
  ["blog/digital-product-passport-guide/", "0.8", "monthly"],
  ["blog/eu-digital-product-passport-business-guide/", "0.8", "monthly"],
  ["blog/digital-product-passport-api/", "0.8", "monthly"],
  ["contribute/", "0.8", "monthly"],
  ["contribute/product/", "0.7", "monthly"],
  ["contribute/store/", "0.7", "monthly"],
  ["contribute/brand/", "0.7", "monthly"],
  ["contribute/bulk/", "0.6", "monthly"],
  ["stores/", "0.5", "weekly"],
  ["brands/", "0.5", "weekly"],
  ["contact/", "0.5", "yearly"],
  ["privacy/", "0.3", "yearly"],
  ["terms/", "0.3", "yearly"],
];

export function GET() {
  const dates = new Map(staticPosts.map(p => [`blog/${p.slug}/`, p.published.slice(0,10)]));
  for (const path of ["", "pricing/", "docs/rate-limits/", "blog/", "barcode-api-for-inventory/", "docs/errors/"]) dates.set(path, "2026-11-04");
  dates.set("barcode-validator/", "2026-11-12");
  dates.set("", "2026-11-12");
  dates.set("blog/", "2026-11-24");
  for (const path of ["barcode-scanner/", "barcode-generator/", "barcode-validator/", "pricing/", "docs/barcode-tools/", ""]) dates.set(path, "2026-11-23");
  const urls = pages.map(([page, priority, changefreq]) => {
    const lastmod = dates.get(page);
    return `<url><loc>https://barcodenest.com/${page}</loc>${lastmod ? `<lastmod>${lastmod}</lastmod>` : ""}<changefreq>${changefreq}</changefreq><priority>${priority}</priority></url>`;
  }).join("");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
}
