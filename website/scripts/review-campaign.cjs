const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs/promises');
(async()=>{
  const {entries}=await import('../campaign/schedule.mjs');
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  const page=await browser.newPage();
  await fs.mkdir('generated/review',{recursive:true});
  const failures=[];
  for(const width of [375,768,1440]) {
    await page.setViewportSize({width,height:900});
    for(const entry of entries) {
      await page.goto(`http://127.0.0.1:4327/blog/${entry.slug}/`,{waitUntil:'networkidle'});
      const state=await page.evaluate(()=>({
        title:document.querySelector('h1')?.textContent,
        overflow:document.documentElement.scrollWidth>innerWidth,
        h1:document.querySelectorAll('h1').length,
        brokenAnchors:[...document.querySelectorAll('a[href^="#"]')].filter(a=>!document.getElementById(a.hash.slice(1))).map(a=>a.hash),
      }));
      if(state.overflow||state.h1!==1||state.brokenAnchors.length) failures.push({width,slug:entry.slug,...state});
    }
    await page.goto('http://127.0.0.1:4327/blog/best-barcode-lookup-apis-2026/');
    await page.screenshot({path:`generated/review/article-${width}.png`,fullPage:true});
  }
  await browser.close();
  console.log(JSON.stringify({pages:30,failures}));
  if(failures.length) process.exitCode=1;
})();
