import { readFile, writeFile, mkdir, unlink } from 'node:fs/promises';
import { entries } from '../campaign/schedule.mjs';
// Bundle rendered HTML only in the server module, never the public asset directory.
const html = {};
for (const entry of entries) {
  const path = new URL(`../dist/blog/${entry.slug}/index.html`, import.meta.url);
  html[entry.slug] = await readFile(path, 'utf8');
  await unlink(path);
}
await mkdir(new URL('../generated/', import.meta.url), { recursive:true });
await writeFile(new URL('../generated/campaign.mjs', import.meta.url), `export default ${JSON.stringify(html)};\n`);
