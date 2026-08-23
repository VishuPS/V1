import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const dist = fileURLToPath(new URL("../dist/", import.meta.url));
if (!existsSync(dist)) throw new Error("dist is missing; run pnpm build before pnpm check:seo");

const htmlFiles = [];
function walk(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) walk(path);
    else if (entry.name.endsWith(".html")) htmlFiles.push(path);
  }
}
walk(dist);

const failures = [];
const titles = new Map();
const descriptions = new Map();
const privatePrefixes = ["admin/", "account/", "api-keys/", "billing/", "contributions/", "dashboard/", "login/", "oauth-complete/", "onboarding/", "profile/", "register/", "settings/"];
const value = (html, pattern) => html.match(pattern)?.[1]?.trim();

for (const file of htmlFiles) {
  const route = relative(dist, file).split(sep).join("/").replace(/index\.html$/, "");
  const html = readFileSync(file, "utf8");
  const isPrivate = privatePrefixes.some((prefix) => route.startsWith(prefix)) || route === "404.html";
  const title = value(html, /<title>([\s\S]*?)<\/title>/i);
  const description = value(html, /<meta\s+name="description"\s+content="([^"]*)"/i);
  const robots = value(html, /<meta\s+name="robots"\s+content="([^"]*)"/i);
  const h1Count = (html.match(/<h1(?:\s|>)/gi) || []).length;

  if (!title) failures.push(`${route || "/"}: missing title`);
  if (!description) failures.push(`${route || "/"}: missing meta description`);
  if (!isPrivate && h1Count !== 1) failures.push(`${route || "/"}: expected one H1, found ${h1Count}`);
  if (!isPrivate && !value(html, /<link\s+rel="canonical"\s+href="([^"]*)"/i)) failures.push(`${route || "/"}: missing canonical`);
  if (!value(html, /<meta\s+property="og:image"\s+content="([^"]*)"/i)) failures.push(`${route || "/"}: missing Open Graph image`);
  if (isPrivate && !robots?.includes("noindex")) failures.push(`${route || "/"}: private page is indexable`);
  if (!isPrivate && robots?.includes("noindex")) failures.push(`${route || "/"}: public page is noindex`);

  if (!isPrivate && title) {
    if (titles.has(title)) failures.push(`${route || "/"}: duplicate title also used by ${titles.get(title)}`);
    titles.set(title, route || "/");
  }
  if (!isPrivate && description) {
    if (descriptions.has(description)) failures.push(`${route || "/"}: duplicate description also used by ${descriptions.get(description)}`);
    descriptions.set(description, route || "/");
  }

  for (const match of html.matchAll(/href="([^"]+)"/gi)) {
    const href = match[1];
    if (href.startsWith("#") || href.startsWith("mailto:") || href.startsWith("tel:")) continue;
    const target = new URL(href, "https://barcodenest.com/");
    if (target.origin !== "https://barcodenest.com") continue;
    const pathname = decodeURIComponent(target.pathname);
    const candidates = pathname.endsWith("/")
      ? [join(dist, pathname, "index.html")]
      : [join(dist, pathname), join(dist, pathname, "index.html"), join(dist, `${pathname}.html`)];
    if (!candidates.some(existsSync)) failures.push(`${route || "/"}: broken internal link ${href}`);
  }
}

for (const asset of ["robots.txt", "sitemap.xml", "favicon.svg", "barcode-lookup-api-social.svg"]) {
  if (!existsSync(join(dist, asset))) failures.push(`dist/${asset}: missing`);
}
const sitemap = readFileSync(join(dist, "sitemap.xml"), "utf8");
for (const route of ["barcode-api/", "upc-api/", "ean-api/", "gtin-api/", "barcode-database/", "contribute/", "contribute/product/", "contribute/store/", "contribute/brand/", "contribute/bulk/", "docs/barcode-lookup/", "docs/authentication/", "docs/rate-limits/", "docs/errors/"]) {
  if (!sitemap.includes(`https://barcodenest.com/${route}`)) failures.push(`sitemap: missing ${route}`);
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(`SEO checks passed for ${htmlFiles.length} generated HTML pages.`);
