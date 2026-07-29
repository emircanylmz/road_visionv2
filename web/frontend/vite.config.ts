import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Geliştirmede /api ve /healthz doğrudan API'ye (127.0.0.1:8800) proxy'lenir;
// üretimde aynı yolları nginx üstlenir (web/frontend/nginx.conf).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8800",
      "/healthz": "http://127.0.0.1:8800",
    },
  },
});
