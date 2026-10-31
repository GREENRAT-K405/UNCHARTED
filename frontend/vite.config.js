import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";

// Backend runs on :8000. --host exposes the dev server on the LAN for the two-laptop demo.
// Browsers only allow camera/mic on https or localhost, so a second laptop opening
// http://<lan-ip>:5173 gets no media at all. `npm run dev:https` serves a self-signed
// https origin instead (accept the warning once per browser).
export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "https" ? [basicSsl()] : [])],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
}));
