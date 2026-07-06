import { defineConfig, devices } from "@playwright/test";

const browserPort = process.env.DI_RAG_BROWSER_PORT ?? "8917";
const browserBaseURL = `http://127.0.0.1:${browserPort}`;

export default defineConfig({
  testDir: "./tests/browser",
  outputDir: "/tmp/di-rag-playwright-results",
  timeout: 30_000,
  expect: {
    timeout: 5_000
  },
  use: {
    baseURL: browserBaseURL,
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
      `DI_RAG_BROWSER_PORT=${browserPort} DI_RAG_TEST_CONFIG_PATH=/tmp/di-rag-browser-provider-config.json DI_RAG_TEST_RESET_CONFIG=1 .venv/bin/python -m tests.browser_app_server`,
    url: browserBaseURL,
    reuseExistingServer: false,
    timeout: 30_000
  }
});
