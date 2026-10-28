import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 构建产物输出到后端包内，由 FastAPI 托管（单进程部署）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../backend/finsight/web", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
