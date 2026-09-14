// Local-only review harness. It is not imported by the production Worker.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import html from '../generated/campaign.mjs';
import { campaignResponse } from '../campaign/publishing.mjs';
import { start } from '../campaign/schedule.mjs';
const root=resolve('dist');
const env={ASSETS:{fetch:async request=>{
  let pathname=decodeURIComponent(new URL(request.url).pathname);
  if(!extname(pathname)) pathname=pathname.replace(/\/$/,'')+'/index.html';
  const file=resolve(root,'.'+pathname);
  if(!file.startsWith(root+sep)) return new Response('Not found',{status:404});
  try { return new Response(await readFile(file),{headers:{'Content-Type':({'.html':'text/html','.css':'text/css','.js':'text/javascript','.svg':'image/svg+xml','.xml':'application/xml','.woff2':'font/woff2'})[extname(file)]||'application/octet-stream'}}); }
  catch {return new Response('Not found',{status:404});}
}}};
createServer(async(req,res)=>{
  try {
    const request=new Request('http://127.0.0.1:4327'+req.url);
    const response=await campaignResponse(request,env,html,Date.parse(start)+9*86400000)||await env.ASSETS.fetch(request);
    res.writeHead(response.status,Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {res.writeHead(500);res.end('Preview error');}
}).listen(4327,'127.0.0.1',()=>console.log('Campaign review at http://127.0.0.1:4327 (all publication dates simulated locally)'));
