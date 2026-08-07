const pages = [
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
  ["pricing/", "0.9", "monthly"],
  ["data/", "0.7", "monthly"],
  ["contact/", "0.5", "yearly"],
  ["privacy/", "0.3", "yearly"],
  ["terms/", "0.3", "yearly"],
];

export function GET() {
  const lastmod = new Date().toISOString().slice(0, 10);
  const urls = pages.map(([page, priority, changefreq]) => {
    return `<url><loc>https://barcodenest.com/${page}</loc><lastmod>${lastmod}</lastmod><changefreq>${changefreq}</changefreq><priority>${priority}</priority></url>`;
  }).join("");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
}
