import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  retries: 0,
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  use: { baseURL: "http://localhost:4173/", trace: "retain-on-failure" },
  webServer: {
    command: "npm run build && npm run preview -- --port 4173 --strictPort",
    url: "http://localhost:4173/",
    reuseExistingServer: false,
  },
});
