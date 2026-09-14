import html from './generated/campaign.mjs';
import { campaignResponse } from './campaign/publishing.mjs';
export default {
  async fetch(request, env) {
    const campaign = await campaignResponse(request, env, html);
    if (campaign) return campaign;
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);

    if (parts.length === 2 && parts[0] === "stores") {
      url.pathname = "/store-profile/";
      return env.ASSETS.fetch(new Request(url, request));
    }

    if (parts.length === 2 && parts[0] === "brands") {
      url.pathname = "/brand-profile/";
      return env.ASSETS.fetch(new Request(url, request));
    }

    return env.ASSETS.fetch(request);
  },
};
