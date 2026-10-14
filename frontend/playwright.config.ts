import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:5191",
    viewport: { width: 1440, height: 1100 },
    trace: "retain-on-failure",
    channel: process.env.COMMERCE_BROWSER_CHANNEL || undefined,
  },
  webServer: [
    {
      command:
        ".venv/bin/python scripts/dev_backend.py --ephemeral --port 8091",
      cwd: "..",
      url: "http://127.0.0.1:8091/health/ready",
      timeout: 30000,
    },
    {
      command: "pnpm dev --port 5191 --strictPort",
      url: "http://127.0.0.1:5191",
      env: { VITE_API_PROXY_TARGET: "http://127.0.0.1:8091" },
      timeout: 30000,
    },
  ],
});
