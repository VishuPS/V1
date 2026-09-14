import { defineConfig } from "astro/config";

export default defineConfig({
  cacheDir: './.astro/cache',
  site: "https://barcodenest.com",
  output: "static",
  build: { format: "directory" },
});
