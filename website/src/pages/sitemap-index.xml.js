const pages = ["", "docs/", "pricing/", "data/", "contact/", "privacy/", "terms/"];
export function GET() {
  const urls = pages.map((page) => `<url><loc>https://barcodenest.com/${page}</loc></url>`).join("");
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`, {
    headers: { "Content-Type": "application/xml" },
  });
}
