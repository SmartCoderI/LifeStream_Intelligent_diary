import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // the Python API (uv run lifestream serve) runs on 8000
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
