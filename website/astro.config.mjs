import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://barcodenest.com",
  output: "static",
  build: { format: "directory" },
});
