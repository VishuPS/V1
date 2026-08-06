export const API_ORIGIN = "https://api.barcodenest.com";
export const API = `${API_ORIGIN}/v1`;

export type AuthUser = {
  id: string;
  name: string;
  email: string;
  company: string | null;
  is_admin: boolean;
  current_plan: string;
  api_key_status: "active" | "revoked" | "none";
  account_status: "active" | "disabled";
  created_at: string;
};

export type AuthState =
  | { status: "loading"; user: null }
  | { status: "authenticated"; user: AuthUser }
  | { status: "unauthenticated"; user: null }
  | { status: "error"; user: null; message: string };

let state: AuthState = { status: "loading", user: null };
let verification: Promise<AuthState> | null = null;
let refreshRequest: Promise<boolean> | null = null;

function publish(next: AuthState): AuthState {
  state = next;
  window.dispatchEvent(new CustomEvent("barcodenest:auth", { detail: next }));
  return next;
}

async function refreshSession(): Promise<boolean> {
  if (!refreshRequest) {
    refreshRequest = fetch(`${API}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then((response) => response.ok).catch(() => false).finally(() => { refreshRequest = null; });
  }
  return refreshRequest;
}

export async function authFetch(path: string, options: RequestInit = {}): Promise<Response> {
  let response = await fetch(`${API}${path}`, { ...options, credentials: "include" });
  if (response.status === 401 && !path.startsWith("/auth/") && await refreshSession()) {
    response = await fetch(`${API}${path}`, { ...options, credentials: "include" });
  }
  return response;
}

export function getCachedAuthState(): AuthState { return state; }

export async function getAuthState(force = false): Promise<AuthState> {
  if (!force && state.status !== "loading") return state;
  if (!force && verification) return verification;
  state = { status: "loading", user: null };
  verification = (async () => {
    try {
      const response = await authFetch("/me");
      if (response.status === 401) return publish({ status: "unauthenticated", user: null });
      if (!response.ok) return publish({ status: "error", user: null, message: "Account verification is temporarily unavailable." });
      return publish({ status: "authenticated", user: await response.json() as AuthUser });
    } catch {
      return publish({ status: "error", user: null, message: "We could not reach the account service. Please try again." });
    } finally { verification = null; }
  })();
  return verification;
}

export async function requireUser(): Promise<AuthUser | null> {
  const result = await getAuthState();
  if (result.status === "unauthenticated") {
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace(`/login/?next=${next}`);
    return null;
  }
  if (result.status === "error") {
    const loading = document.querySelector<HTMLElement>("#protected-loading");
    if (loading) { loading.className = "protected-state error"; loading.textContent = result.message; }
    return null;
  }
  if (result.status !== "authenticated") return null;
  document.querySelector<HTMLElement>("#protected-loading")?.setAttribute("hidden", "");
  const app = document.querySelector<HTMLElement>("#protected-app"); if (app) app.hidden = false;
  return result.user;
}

export async function signOut(): Promise<void> {
  const response = await fetch(`${API}/auth/logout`, { method: "POST", credentials: "include" });
  if (!response.ok) throw new Error("Sign out could not be completed. Please try again.");
  publish({ status: "unauthenticated", user: null });
  location.assign("/");
}
