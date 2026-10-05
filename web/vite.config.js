import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
import Components from "unplugin-vue-components/vite";
import { defineConfig } from "vite";

const api = process.env.BID_API_URL || "http://127.0.0.1:8000";

// The console is served under /app so its routes never collide with API paths.
// Element Plus components, services and their styles are imported on demand.
export default defineConfig({
  base: "/app/",
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()], dts: false }),
    Components({ resolvers: [ElementPlusResolver()], dts: false }),
  ],
  // One chunk keeps Element Plus tree-shaken (a vendor split pulls in the whole library).
  build: { chunkSizeWarningLimit: 650 },
  // The console calls same-origin API paths, so everything outside /app goes to the API.
  server: { proxy: { "^/(?!app(?:/|$))": api } },
});
