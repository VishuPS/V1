import test from 'node:test';
import assert from 'node:assert/strict';
import { lookupProduct } from '../src/content/examples/barcode-lookup.mjs';
import { rss } from '../campaign/publishing.mjs';
test('published helper keeps leading zeros and passes the server key',async()=>{
  const result=await lookupProduct('012345678905','test-key',async(url,options)=>{
    assert.equal(url,'https://api.barcodenest.com/v1/products/012345678905');
    assert.equal(options.headers['X-API-Key'],'test-key');
    assert.ok(options.signal instanceof AbortSignal);
    return Response.json({found:true,product:{name:'Example'}});
  });
  assert.equal(result.product.name,'Example');
});
test('only a product miss becomes null; API errors stay errors',async()=>{
  assert.equal(await lookupProduct('012345678905','key',async()=>new Response(null,{status:404})),null);
  for(const status of [400,401,402,429,500]) {
    await assert.rejects(lookupProduct('012345678905','key',async()=>Response.json({error:{code:'example',message:'Failed'}},{status})),e=>e.status===status && e.code==='example');
  }
  await assert.rejects(lookupProduct(12345678905,'key'),TypeError);
  await assert.rejects(lookupProduct('012345678905',''),/not configured/);
});
test('RSS includes current static guides without publishing future content',()=>{
  const before=rss(Date.parse('2026-11-03T23:59:59Z'));
  assert.ok(before.includes('barcode-lookup-python'));
  assert.ok(!before.includes('barcode-lookup-javascript'));
  const today=rss(Date.parse('2026-11-04T00:00:00Z'));
  assert.ok(today.includes('barcode-lookup-javascript'));
  assert.ok(today.includes('barcode-lookup-not-found'));
});
