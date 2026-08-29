import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: true,
  reporter: [["list"], ["html", { outputFolder: "playwright-report" }]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "python3 -m uvicorn apps.api.src.main:app --host 127.0.0.1 --port 8765",
    url: "http://127.0.0.1:8765/readyz",
    reuseExistingServer: true,
    timeout: 30_000,
    env: {
      APP_ENV: "e2e",
      PUBLIC_DATA_MODE: "fixture",
      HIGHLIGHTS_DEMO_DATE: "2026-06-12",
      LLM_MODE: "mock",
      RUNTIME_PROFILE: "template",
      HERMES_LITE_MODE: "off",
      AUTH_REQUIRED: "false",
    },
  },
});
