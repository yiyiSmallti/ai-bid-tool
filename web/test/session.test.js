import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { SESSION_KEY } from "../src/session-storage.js";

class Storage {
  getItem(key) { return Object.hasOwn(this, key) ? this[key] : null; }
  setItem(key, value) { this[key] = String(value); }
  removeItem(key) { delete this[key]; }
  clear() { for (const key of Object.keys(this)) delete this[key]; }
}
globalThis.sessionStorage = new Storage();
globalThis.window = new EventTarget();
window.location = { origin: "http://localhost", pathname: "/app/", search: "", hash: "" };
const historyCalls = [];
globalThis.history = {
  state: { position: 1 },
  replaceState(state, title, url) {
    historyCalls.push({ state, title, url });
    window.location.hash = "";
  },
};
const { ApiError, contextKey, exchangeAccessKey, request, session, takeAccessKeyFragment } = await import("../src/api.js");
const result = (data, { ok = true, command = "auth session create" } = {}) => ({
  ok, command, data, items: [], warnings: [], duration_ms: 0,
  cost: { calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 },
});
const response = (data, status = 200, options) => new Response(JSON.stringify(result(data, options)), { status, headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  session.clear();
  sessionStorage.clear();
  historyCalls.length = 0;
  Object.assign(window.location, { pathname: "/app/", search: "", hash: "" });
});

test("desktop fragment is decoded and scrubbed before the key is exchanged", async () => {
  const key = "a+/%&?=中文".repeat(4);
  Object.assign(window.location, { pathname: "/app/org/tasks", search: "?job=123", hash: `#key=${encodeURIComponent(key)}` });
  const taken = takeAccessKeyFragment();
  assert.equal(taken, key);
  assert.equal(window.location.hash, "");
  assert.deepEqual(historyCalls, [{ state: history.state, title: "", url: "/app/org/tasks?job=123" }]);
  globalThis.fetch = async (path, options) => {
    assert.equal(window.location.hash, "");
    assert.equal(path, "/auth/session");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { access_key: key });
    assert.equal(options.headers.Authorization, undefined);
    return response({ session: "console-token", expires_in: 3600 });
  };
  await exchangeAccessKey(taken);
  assert.equal(sessionStorage.getItem(SESSION_KEY), "console-token");
  assert.equal(session.get(), "console-token");
});

test("empty and malformed key fragments are scrubbed; ordinary anchors stay", () => {
  for (const hash of ["#key", "#key=", "#key=%ZZ"]) {
    window.location.hash = hash;
    assert.equal(takeAccessKeyFragment(), "");
    assert.equal(window.location.hash, "");
  }
  window.location.hash = "#main-content";
  assert.equal(takeAccessKeyFragment(), null);
  assert.equal(window.location.hash, "#main-content");
});

test("authenticated reads send the single session as a bearer and no identity header", async () => {
  session.set("console-token");
  globalThis.fetch = async (path, options) => {
    assert.equal(path, "/auth/session");
    assert.deepEqual(options.headers, { Authorization: "Bearer console-token" });
    return response({ actor_kind: "session", scopes: ["task:read"], expires_at: null }, 200, { command: "auth session show" });
  };
  assert.equal((await request("GET", "/auth/session")).data.actor_kind, "session");
});

test("a missing session never starts an authenticated read", async () => {
  globalThis.fetch = () => assert.fail("fetch should not run");
  await assert.rejects(request("GET", "/auth/session"), (error) => error instanceof ApiError && error.status === 401);
  await assert.rejects(request("GET", "/tasks"), (error) => error instanceof ApiError && error.code === "session_required");
});

test("an invalid key does not store a session or dispatch a signed-out event", async () => {
  let signedOut = 0;
  const listener = () => signedOut++;
  window.addEventListener("bid:signed-out", listener);
  try {
    globalThis.fetch = async () => response({ error: { code: "invalid_access_key", message: "Invalid access key" } }, 401, { ok: false });
    await assert.rejects(exchangeAccessKey("wrong-key"), (error) => error.code === "invalid_access_key");
    assert.equal(session.get(), null);
    assert.equal(signedOut, 0);
  } finally { window.removeEventListener("bid:signed-out", listener); }
});

test("a successful envelope with an invalid session receipt is rejected", async () => {
  globalThis.fetch = async () => response({ session: { token: "wrong-shape" }, expires_in: 3600 });
  await assert.rejects(exchangeAccessKey("access-key"), (error) => error.code === "invalid_response");
  assert.equal(session.get(), null);
});

test("a 401 clears context and signs out even when its body is not JSON", async () => {
  session.set("expired-token");
  sessionStorage.setItem(contextKey("selection"), "123");
  let signedOut = 0;
  const listener = () => signedOut++;
  window.addEventListener("bid:signed-out", listener);
  try {
    globalThis.fetch = async () => new Response("Unauthorized", { status: 401 });
    await assert.rejects(request("GET", "/tasks"));
    assert.equal(session.get(), null);
    assert.equal(sessionStorage.getItem(contextKey("selection")), null);
    assert.equal(signedOut, 1);
  } finally { window.removeEventListener("bid:signed-out", listener); }
});

test("reset aborts in-flight reads and removes only console context", async () => {
  session.set("old-token");
  sessionStorage.setItem(contextKey("selection"), "123");
  sessionStorage.setItem("unrelated", "keep");
  let signal;
  globalThis.fetch = (path, options) => {
    signal = options.signal;
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true }));
  };
  const pending = request("GET", "/tasks");
  session.clear();
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(signal.aborted, true);
  assert.equal(sessionStorage.getItem(contextKey("selection")), null);
  assert.equal(sessionStorage.getItem("unrelated"), "keep");
});

test("route guard accepts a verified human session", async () => {
  const { sessionRedirect } = await import("../src/session-guard.js");
  session.set("console-token");
  globalThis.fetch = async () => response({ actor_kind: "session", scopes: ["task:read"], expires_at: null });
  assert.equal(await sessionRedirect(), undefined);
  assert.equal(session.get(), "console-token");
});

test("route guard rejects nonhuman and malformed identity receipts", async () => {
  const { sessionRedirect } = await import("../src/session-guard.js");
  for (const data of [{ actor_kind: "token", scopes: [] }, { actor_kind: "session", scopes: "task:read" }, { actor_kind: "session", scopes: [null] }]) {
    session.set("console-token");
    globalThis.fetch = async () => response(data);
    assert.equal(await sessionRedirect(), "/login");
    assert.equal(session.get(), null);
  }
});

test("route guard redirects an expired session and clears the token", async () => {
  const { sessionRedirect } = await import("../src/session-guard.js");
  session.set("expired-token");
  globalThis.fetch = async () => response({ error: { code: "invalid_access_key", message: "Invalid access key" } }, 401, { ok: false });
  assert.equal(await sessionRedirect(), "/login");
  assert.equal(session.get(), null);
});

test("route guard blocks navigation on transient failures while retaining the token", async () => {
  const { sessionRedirect } = await import("../src/session-guard.js");
  session.set("console-token");
  for (const failedFetch of [async () => response({ error: { code: "unavailable", message: "Unavailable" } }, 503, { ok: false }), async () => { throw new TypeError("Failed to fetch"); }]) {
    globalThis.fetch = failedFetch;
    assert.deepEqual(await sessionRedirect(), { path: "/login", query: { unavailable: "1" } });
    assert.equal(session.get(), "console-token");
  }
});
