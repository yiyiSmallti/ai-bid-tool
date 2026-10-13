// The console takes the access key from the URL fragment (the desktop shell opens
// /app/#key=...), exchanges it for a session and removes the fragment before navigating.
export async function openConsole(page, path = "/org/tasks") {
  const key = process.env.E2E_ACCESS_KEY ?? "";
  if (key.length < 32) throw new Error("E2E_ACCESS_KEY must hold the server BID_ACCESS_KEY (at least 32 characters)");
  await page.goto(`/app${path}#key=${encodeURIComponent(key)}`);
}
