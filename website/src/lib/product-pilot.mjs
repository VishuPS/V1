import products from '../content/product-pilot.json' with { type: 'json' };

export { products };
export const productPath = product => `/product/${product.barcode}/`;
export const productUrl = product => `https://barcodenest.com${productPath(product)}`;
export const aliasMap = Object.fromEntries(products.flatMap(p => p.aliases.map(a => [a, productPath(p)])));
export const sitemapXml = () => `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${products.map(p => `<url><loc>${productUrl(p)}</loc><lastmod>${p.modified}</lastmod></url>`).join('')}</urlset>`;

export function productRedirect(request) {
  const url = new URL(request.url);
  const match = url.pathname.match(/^\/product\/([0-9]{8,14})(?:\/|\/index\.html|\.html)?$/);
  if (!match || !aliasMap[match[1]]) return null;
  const target = aliasMap[match[1]];
  if (url.pathname === target) return null;
  return new Response(null, {status: 301, headers: {Location: target + url.search, 'Cache-Control': 'public, max-age=3600'}});
}

export function productSchema(p) {
  return {
    '@context': 'https://schema.org', '@type': 'Product', '@id': `${productUrl(p)}#product`,
    name: p.name, gtin14: p.gtin, url: productUrl(p),
    brand: {'@type': 'Brand', name: p.brand},
    ...(p.categories.length ? {category: p.categories.join(', ')} : {}),
    ...(p.quantity ? {size: p.quantity} : {}),
    identifier: p.barcode,
  };
}
