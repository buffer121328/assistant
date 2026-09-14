import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/main/index.ts")
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/preload/index.ts")
      }
    }
  },
  renderer: {
    root: resolve(__dirname, "src/renderer"),
    plugins: [react()],
    server: { host: "127.0.0.1" },
    build: {
      rollupOptions: {
        input: {
          employee: resolve(__dirname, "src/renderer/index.html"),
          admin: resolve(__dirname, "src/renderer/admin.html"),
          web: resolve(__dirname, "src/renderer/web.html")
        }
      }
    }
  }
});
