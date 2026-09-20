import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// The backend runs on :8000 (Windows native, or inside WSL).
export default defineConfig({
  plugins: [vue()],
  resolve: {
    // Mirrors the `paths` entry in tsconfig.json; Vite does not read tsconfig paths.
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Keeps cookies and CORS simple in development.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
