import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
export const collections = {
  campaign: defineCollection({
    loader: glob({ pattern: '*.md', base: './src/content/campaign' }),
    schema: z.object({ description: z.string().min(60).max(180), keywords: z.string() }),
  }),
};
