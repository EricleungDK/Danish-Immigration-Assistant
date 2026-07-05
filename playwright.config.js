import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  outputDir: "/tmp/di-rag-playwright-results",
  timeout: 30_000,
  expect: {
    timeout: 5_000
  },
  use: {
    baseURL: "http://127.0.0.1:8917",
    trace: "retain-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: {
    command:
      "DI_RAG_TEST_CONFIG_PATH=/tmp/di-rag-browser-provider-config.json DI_RAG_TEST_RESET_CONFIG=1 .venv/bin/python -m tests.browser_app_server",
    url: "http://127.0.0.1:8917",
    reuseExistingServer: false,
    timeout: 30_000
  }
});
