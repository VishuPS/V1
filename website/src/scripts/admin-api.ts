export const ADMIN_API = "https://api.barcodenest.com/v1/admin";
const AUTH_API = "https://api.barcodenest.com/v1/auth";

async function refreshSession(): Promise<boolean> {
  const response = await fetch(`${AUTH_API}/refresh`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  return response.ok;
}

export async function adminFetch(path: string, options: RequestInit = {}): Promise<Response> {
  let response = await fetch(`${ADMIN_API}${path}`, { ...options, credentials: "include" });
  if (response.status === 401 && await refreshSession()) {
    response = await fetch(`${ADMIN_API}${path}`, { ...options, credentials: "include" });
  }
  return response;
}

export async function requireAdmin(probe = "/dashboard"): Promise<Response | null> {
  const { getAuthState } = await import("./auth-context");
  const auth = await getAuthState();
  if (auth.status === "unauthenticated") {
    const destination = encodeURIComponent(location.pathname + location.search);
    location.replace(`/login/?next=${destination}`);
    return null;
  }
  if (auth.status === "authenticated" && !auth.user.is_admin) {
    document.querySelector<HTMLElement>("#admin-loading")?.setAttribute("hidden", "");
    const denied = document.querySelector<HTMLElement>("#admin-denied"); if (denied) denied.hidden = false;
    return null;
  }
  if (auth.status === "error") throw new Error(auth.message);
  const response = await adminFetch(probe);
  if (response.status === 401) {
    const destination = encodeURIComponent(location.pathname + location.search);
    location.assign(`/login/?next=${destination}`);
    return null;
  }
  document.querySelector<HTMLElement>("#admin-loading")?.setAttribute("hidden", "");
  if (response.status === 403) {
    const denied = document.querySelector<HTMLElement>("#admin-denied");
    if (denied) denied.hidden = false;
    return null;
  }
  if (!response.ok) throw new Error("The admin service could not be loaded.");
  const app = document.querySelector<HTMLElement>("#admin-app");
  if (app) app.hidden = false;
  return response;
}

export function formatDate(value: string | null, includeTime = false): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    ...(includeTime ? { timeStyle: "short" as const } : {}),
  }).format(new Date(value));
}

export async function responseMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body?.error?.message || "The action could not be completed.";
  } catch {
    return "The action could not be completed.";
  }
}
