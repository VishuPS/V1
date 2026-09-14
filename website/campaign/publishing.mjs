import { entryForPath, publishedEntries } from './schedule.mjs';

export const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const origin = 'https://barcodenest.com';
export function publicHtml(html, now) {
  // Remove links to unreleased campaign posts, keeping their descriptive text.
  return html.replace(/<a\b([^>]*\bhref=["']([^"']+)["'][^>]*)>([\s\S]*?)<\/a>/gi, (tag, _attrs, href, body) => {
    const url = new URL(href, origin);
    const entry = url.origin === origin ? entryForPath(url.pathname) : undefined;
    return entry && Date.parse(entry.published) > now ? body : tag;
  });
}
export function indexSection(now) {
  const posts = publishedEntries(now).reverse();
  if (!posts.length) return '';
  const rows = posts.map(p => `<a class="blog-archive-row" href="/blog/${p.slug}/"><div class="article-card-meta"><span>Developer guide</span><time datetime="${p.published}">${p.published.slice(0,10)}</time></div><h3>${escape(p.title)}</h3><span class="archive-arrow" aria-hidden="true">→</span></a>`).join('');
  const schema = {'@context':'https://schema.org','@type':'ItemList', itemListElement:posts.map((p,i)=>({'@type':'ListItem',position:i+1,url:`${origin}/blog/${p.slug}/`,name:p.title}))};
  return `<section class="container blog-archive" aria-labelledby="developer-guides"><div class="blog-archive-head"><div><div class="eyebrow">API engineering</div><h2 id="developer-guides">Developer guides</h2></div></div>${rows}</section><script type="application/ld+json">${JSON.stringify(schema).replace(/</g,'\\u003c')}</script>`;
}
export function sitemapEntries(now) {
  return publishedEntries(now).map(p=>`<url><loc>${origin}/blog/${p.slug}/</loc><lastmod>${p.published}</lastmod></url>`).join('');
}
export function rss(now) {
  return `<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>BarcodeNest Developer Guides</title><link>${origin}/blog/</link><description>Practical barcode API engineering guides</description>${publishedEntries(now).reverse().map(p=>`<item><title>${escape(p.title)}</title><link>${origin}/blog/${p.slug}/</link><guid>${origin}/blog/${p.slug}/</guid><pubDate>${new Date(p.published).toUTCString()}</pubDate></item>`).join('')}</channel></rss>`;
}
export function response(body, type, request, status = 200) {
  return new Response(request.method === 'HEAD' ? null : body, {status, headers:{
    'Content-Type':type, 'Cache-Control':'no-store',
    ...(status === 404 ? {'X-Robots-Tag':'noindex'} : {})
  }});
}
export async function campaignResponse(request, env, html, now = Date.now()) {
  const url = new URL(request.url);
  const entry = entryForPath(url.pathname);
  if (entry) {
    if (Date.parse(entry.published) > now) return response('Not found', 'text/plain; charset=utf-8',request,404);
    const canonicalPath = `/blog/${entry.slug}/`;
    if (url.pathname !== canonicalPath) return new Response(null,{status:308,headers:{Location:canonicalPath,'Cache-Control':'no-store'}});
    return response(publicHtml(html[entry.slug],now),'text/html; charset=utf-8',request);
  }
  if (url.pathname === '/blog/rss.xml') return response(rss(now),'application/rss+xml; charset=utf-8',request);
  if (['/blog/','/blog','/blog/index.html'].includes(url.pathname)) {
    const assetUrl = new URL('/blog/', url);
    const asset = await env.ASSETS.fetch(new Request(assetUrl));
    const body = (await asset.text()).replace('<div id="campaign-index"></div>',indexSection(now));
    return response(publicHtml(body,now),'text/html; charset=utf-8',request,asset.status);
  }
  if (url.pathname === '/sitemap.xml') {
    const asset = await env.ASSETS.fetch(new Request(url));
    return response((await asset.text()).replace('</urlset>',sitemapEntries(now)+'</urlset>'),'application/xml; charset=utf-8',request,asset.status);
  }
  return null;
}
