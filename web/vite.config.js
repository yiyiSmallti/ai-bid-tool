import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const api = process.env.BID_API_URL || "http://127.0.0.1:8000";

// The console is served under /app so its routes never collide with API paths.
export default defineConfig({
  base: "/app/",
  plugins: [vue()],
  server: { proxy: { "/platform": api, "/auth": api } },
});
