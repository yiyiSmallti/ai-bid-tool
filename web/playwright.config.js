import { defineConfig } from "@playwright/test";

// Runs against an already started API that serves the built console (BID_WEB_DIR).
export default defineConfig({
  testDir: "./e2e",
  outputDir: "../data/work/playwright-results",
  timeout: 60_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL,
    browserName: "chromium",
    acceptDownloads: true,
    locale: "zh-CN",
  },
});
