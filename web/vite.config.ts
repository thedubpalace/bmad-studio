import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = "http://127.0.0.1:5024";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    emptyOutDir: true,
    // the shell's CSP is script-src 'self': never inline the module preload polyfill
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  server: {
    host: "127.0.0.1",
    proxy: { "/api": backend, "/health": backend },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
