const pages = ["", "docs/", "pricing/", "data/", "contact/", "privacy/", "terms/"];

export function GET() {
  const lastmod = new Date().toISOString().slice(0, 10);
  const urls = pages.map((page) => {
    const priority = page === "" ? "1.0" : page === "docs/" || page === "pricing/" ? "0.9" : "0.6";
    return `<url><loc>https://barcodenest.com/${page}</loc><lastmod>${lastmod}</lastmod><changefreq>weekly</changefreq><priority>${priority}</priority></url>`;
  }).join("");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`, {
    headers: { "Content-Type": "application/xml; charset=utf-8" },
  });
}
