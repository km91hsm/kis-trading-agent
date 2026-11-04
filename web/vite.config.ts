/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버는 `trader web`(127.0.0.1:8787)으로 /api 를 프록시한다. 빌드 결과(dist/)는 Python 쪽이 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
  build: { outDir: "dist" },
  test: { environment: "jsdom", setupFiles: ["src/test/setup.ts"], css: { include: [/tokens\.css/] } },
});
