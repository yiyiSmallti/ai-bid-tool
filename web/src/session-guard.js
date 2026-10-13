import { request, session } from "./api.js";

export async function sessionRedirect() {
  if (!session.get()) return "/login";
  try {
    const result = await request("GET", "/auth/session");
    if (result.data.actor_kind !== "session" || !Array.isArray(result.data.scopes) || !result.data.scopes.every(scope => typeof scope === "string")) {
      session.clear();
      return "/login";
    }
  } catch {
    return session.get() ? { path: "/login", query: { unavailable: "1" } } : "/login";
  }
}
