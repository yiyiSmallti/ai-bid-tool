// Platform sessions live in sessionStorage: they expire after 30 minutes anyway,
// and closing the tab signs the operator out.
const KEY = "bid.platform.session";

export const session = {
  get: () => sessionStorage.getItem(KEY),
  set: (value) => sessionStorage.setItem(KEY, value),
  clear: () => sessionStorage.removeItem(KEY),
};

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = session.get();
  if (token) headers.Authorization = `Bearer ${token}`;
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
    if (response.status === 401 && path.startsWith("/platform/") && path !== "/platform/auth/login") {
      session.clear();
      window.dispatchEvent(new Event("bid:signed-out"));
    }
    throw new ApiError(response.status, error.code ?? "request_failed", error.message ?? "请求失败");
  }
  return payload;
}

export const money = (value) =>
  value === null || value === undefined ? "—" : `$${Number(value).toFixed(4)}`;
export const count = (value) => Number(value ?? 0).toLocaleString("zh-CN");
