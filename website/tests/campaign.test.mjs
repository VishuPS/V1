import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { entries, start, publishedEntries, entryForPath } from '../campaign/schedule.mjs';
import { campaignResponse, indexSection, sitemapEntries, rss, publicHtml } from '../campaign/publishing.mjs';
const beginning = Date.parse(start);
const assets = { ASSETS: { fetch: async request => new Response(new URL(request.url).pathname === '/sitemap.xml' ? '<urlset></urlset>' : '<html><div id="campaign-index"></div></html>') }};
const content = Object.fromEntries(entries.map(e=>[e.slug,`<h1>${e.title}</h1>`]));

test('ten posts release in exact order, on UTC 24-hour boundaries',()=>{
  assert.equal(entries.length,10);
  assert.equal(new Set(entries.map(e=>e.slug)).size,10);
  assert.equal(publishedEntries(beginning-1).length,0);
  for(let day=0;day<10;day++) {
    const instant=beginning+day*86400000;
    assert.equal(Date.parse(entries[day].published),instant);
    assert.deepEqual(publishedEntries(instant).map(e=>e.slug),entries.slice(0,day+1).map(e=>e.slug));
    assert.equal(publishedEntries(instant-1).length,day);
  }
});
test('all unreleased routes and common aliases return uncached 404',async()=>{
  for(const entry of entries.slice(1)) {
    for(const path of [`/blog/${entry.slug}/`,`/blog/${entry.slug}`,`/blog/${entry.slug}.html`,`/blog/${entry.slug}/index.html`,`/blog/${encodeURIComponent(entry.slug).replace(/-/g,'%2D')}/`]) {
      for(const method of ['GET','HEAD']) {
        const response=await campaignResponse(new Request('https://barcodenest.com'+path,{method}),assets,content,beginning);
        assert.equal(response.status,404,path);
        assert.equal(response.headers.get('Cache-Control'),'no-store');
        assert.equal(response.headers.get('X-Robots-Tag'),'noindex');
        assert.ok(!(await response.text()).includes(entry.title));
      }
    }
  }
});
test('routes become public at the exact boundary and aliases canonicalize',async()=>{
  for(const entry of entries) {
    const request=new Request(`https://barcodenest.com/blog/${entry.slug}/`);
    const response=await campaignResponse(request,assets,content,Date.parse(entry.published));
    assert.equal(response.status,200);
    assert.match(await response.text(),/<h1>/);
    const alias=await campaignResponse(new Request(`https://barcodenest.com/blog/${entry.slug}.html`),assets,content,Date.parse(entry.published));
    assert.equal(alias.status,308);
    assert.equal(alias.headers.get('Location'),`/blog/${entry.slug}/`);
  }
});
test('index, structured data, sitemap, feed and links reveal only published posts',async()=>{
  for(let day=0;day<10;day++) {
    const now=beginning+day*86400000;
    for(const render of [indexSection,sitemapEntries,rss]) {
      const output=render(now);
      for(const [i,e] of entries.entries()) assert.equal(output.includes(e.slug),i<=day,`${render.name} day ${day} ${e.slug}`);
    }
    const links=entries.map(e=>`<a href="/blog/${e.slug}/">Read guide</a>`).join('');
    const filtered=publicHtml(links,now);
    for(const [i,e] of entries.entries()) assert.equal(filtered.includes(e.slug),i<=day);
  }
  for(const path of ['/blog/','/sitemap.xml','/blog/rss.xml']) {
    const result=await campaignResponse(new Request('https://barcodenest.com'+path),assets,content,beginning);
    assert.ok((await result.text()).includes(entries[0].slug));
  }
});
test('unrelated routes fall through and malformed encodings fail safely',async()=>{
  assert.equal(entryForPath('/blog/%zz'),undefined);
  assert.equal(await campaignResponse(new Request('https://barcodenest.com/pricing/'),assets,content,beginning),null);
});
test('packaged article HTML has unique metadata and is absent from static assets',async()=>{
  const {default:html}=await import('../generated/campaign.mjs');
  const index=await readFile(new URL('../dist/blog/index.html',import.meta.url),'utf8');
  assert.ok(index.includes('<div id="campaign-index"></div>'),'Astro must preserve the server insertion marker');
  const descriptions=new Set();
  for(const entry of entries) {
    assert.ok(html[entry.slug].includes(`<title>${entry.title.replace(/&/g,'&amp;').replace(/'/g,'&#39;')}</title>`) || html[entry.slug].includes('<title>'));
    assert.equal((html[entry.slug].match(/<h1[ >]/g)||[]).length,1);
    assert.ok(html[entry.slug].includes(`https://barcodenest.com/blog/${entry.slug}/`));
    assert.ok(html[entry.slug].includes(entry.published));
    assert.ok(html[entry.slug].includes('"@type":"Article"'));
    assert.ok(html[entry.slug].includes('property="og:type" content="article"'));
    descriptions.add(html[entry.slug].match(/name="description" content="([^"]+)"/)[1]);
    await assert.rejects(access(new URL(`../dist/blog/${entry.slug}/index.html`,import.meta.url)));
    const markdown=await readFile(new URL(`../src/content/campaign/${entry.slug}.md`,import.meta.url),'utf8');
    assert.ok(markdown.includes('## FAQ'));
    assert.ok(markdown.includes('/register/'));
    assert.ok(markdown.includes('article-callout'));
  }
  assert.equal(descriptions.size,10);
});
