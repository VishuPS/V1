import { sitemapXml } from '../lib/product-pilot.mjs';
export function GET() { return new Response(sitemapXml(), {headers:{'Content-Type':'application/xml; charset=utf-8'}}); }
