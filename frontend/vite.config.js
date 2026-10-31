import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend runs on :8000. --host exposes the dev server on the LAN for the two-laptop demo.
// Note: browsers only allow camera/mic on https or localhost; for a LAN demo use
// Chrome's "Insecure origins treated as secure" flag or a tunnel (see README).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
