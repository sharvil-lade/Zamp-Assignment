import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// React is the frontend: the backend serves this bundle at the root and answers
// everything under /api. In development the two run on separate ports, which is
// why the dev origin has to appear in CORS_ORIGINS (backend/config.py).
export default defineConfig(() => ({
  base: "/",
  plugins: [react()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/__tests__/setup.js",
    css: false,
  },
}));
