import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { products, productPath, productUrl, productRedirect, productSchema, sitemapXml } from '../src/lib/product-pilot.mjs';

test('pilot has 100 distinct attributable complete public records with valid checksums', () => {
  assert.equal(products.length,100);
  assert.equal(new Set(products.map(p=>p.gtin)).size,100);
  const allowed=['gtin','barcode','aliases','name','brand','quantity','categories','countries','modified','sources'];
  for (const p of products) {
    assert.ok(p.brand && p.name && (p.quantity || p.categories.length));
    assert.ok(p.sources.length);
    assert.ok(Object.keys(p).every(k=>allowed.includes(k)));
    assert.match(p.gtin,/^\d{14}$/);
    const sum=[...p.gtin.slice(0,-1)].reverse().reduce((n,v,i)=>n+Number(v)*(i%2?1:3),0);
    assert.equal(Number(p.gtin.at(-1)),(10-sum%10)%10,p.gtin);
    assert.ok(p.aliases.includes(p.barcode));
    assert.ok(p.sources.every(s=>new URL(s.source_url).protocol==='https:' && s.license));
  }
});

test('equivalent barcode formats redirect permanently without loops; unknowns do not', () => {
  for(const p of products) {
    for(const alias of p.aliases) {
      for(const path of [`/product/${alias}/`,`/product/${alias}`,`/product/${alias}.html`,`/product/${alias}/index.html`]) {
        const r=productRedirect(new Request(`https://barcodenest.com${path}?utm_source=test`));
        if(path===productPath(p)) assert.equal(r,null);
        else { assert.equal(r.status,301); assert.equal(r.headers.get('Location'),`${productPath(p)}?utm_source=test`); }
      }
    }
  }
  for(const path of ['/product/00000000000000/','/product/abc/','/product/../','/products/']) assert.equal(productRedirect(new Request('https://barcodenest.com'+path)),null);
});

test('sitemap includes only canonical pages and real record modification dates',()=>{
  const xml=sitemapXml();
  assert.equal((xml.match(/<url>/g)||[]).length,100);
  for(const p of products) {
    assert.ok(xml.includes(`<loc>${productUrl(p)}</loc><lastmod>${p.modified}</lastmod>`));
    const schema=productSchema(p);
    assert.equal(schema.gtin14,p.gtin);
    assert.ok(!schema.offers && !schema.aggregateRating);
  }
});

test('built product pages have canonical identity, visible attribution and valid JSON-LD',()=>{
  for(const p of products) {
    const file=new URL(`../dist/product/${p.barcode}/index.html`,import.meta.url);
    assert.ok(existsSync(file));
    const html=readFileSync(file,'utf8');
    assert.ok(html.includes(`rel="canonical" href="${productUrl(p)}"`));
    assert.equal((html.match(/<h1(?:\s|>)/g)||[]).length,1);
    assert.ok(html.includes(p.gtin));
    assert.ok(!html.includes('noindex'));
    const schemas=[...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(m=>JSON.parse(m[1]));
    assert.ok(JSON.stringify(schemas).includes('BreadcrumbList'));
    assert.ok(JSON.stringify(schemas).includes('Product'));
    for(const s of p.sources) assert.ok(html.includes(new URL(s.source_url).hostname));
  }
});
