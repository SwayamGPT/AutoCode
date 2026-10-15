import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The Python backend serves the built UI from agent_platform/static.
// `npm run dev` proxies API calls to a backend started with `python app.py serve`.
const backend = process.env.AUTOCODE_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../agent_platform/static", emptyOutDir: true },
  server: { proxy: { "/api": backend, "/a2a": backend } },
});
