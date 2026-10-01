import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { cspMeta } from "./plugins/csp.ts";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), cspMeta()],
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "plugins/**/*.test.ts"],
  },
});
