import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port and host. See https://tauri.app/start/frontend/vite/
const host = process.env.TAURI_DEV_HOST;
const sidecarPort = process.env.VITE_SIDECAR_PORT || "8765";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? { protocol: "ws", host, port: 1421 }
      : undefined,
    watch: { ignored: ["**/src-tauri/**", "**/sidecar/**"] },
  },
  define: {
    // 浏览器模式(无 Tauri)用这个直连 sidecar
    "import.meta.env.VITE_SIDECAR_PORT": JSON.stringify(sidecarPort),
  },
  build: {
    target: "es2021",
    minify: "esbuild",
    sourcemap: false,
  },
});
