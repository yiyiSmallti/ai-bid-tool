// Sessions live in sessionStorage: they expire on the server anyway, and closing
// the tab signs the user out. Platform and org sessions are kept apart.
const PLATFORM_KEY = "bid.platform.session";
const ORG_KEY = "bid.org.session";

export const session = {
  get: () => sessionStorage.getItem(PLATFORM_KEY),
  set: (value) => sessionStorage.setItem(PLATFORM_KEY, value),
  clear: () => sessionStorage.removeItem(PLATFORM_KEY),
};

export const orgSession = {
  get: () => JSON.parse(sessionStorage.getItem(ORG_KEY) ?? "null"),
  set: (value) => sessionStorage.setItem(ORG_KEY, JSON.stringify(value)),
  clear: () => sessionStorage.removeItem(ORG_KEY),
};

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const PUBLIC = new Set(["/platform/auth/login", "/auth/login", "/auth/orgs", "/auth/setup-password"]);

export async function request(method, path, body, { org = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (org) {
    const current = orgSession.get();
    if (current) {
      headers.Authorization = `Bearer ${current.session}`;
      headers["X-Org-Id"] = current.orgId;
    }
  } else {
    const token = session.get();
    if (token && path.startsWith("/platform/")) headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError(response.status, "invalid_response", "服务返回了无法识别的内容");
  }
  if (!response.ok || !payload.ok) {
    const error = payload?.data?.error ?? {};
    if (response.status === 401 && !PUBLIC.has(path)) {
      (org ? orgSession : session).clear();
      window.dispatchEvent(new CustomEvent("bid:signed-out", { detail: org ? "org" : "platform" }));
    }
    throw new ApiError(response.status, error.code ?? "request_failed", error.message ?? "请求失败");
  }
  return payload;
}

export const money = (value, currency = "") => {
  if (value === null || value === undefined) return "—";
  const amount = Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  return currency ? `${amount} ${currency}` : amount;
};
export const count = (value) => Number(value ?? 0).toLocaleString("zh-CN");
