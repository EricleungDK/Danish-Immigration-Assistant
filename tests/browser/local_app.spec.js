import { expect, test } from "@playwright/test";

test("first launch shows product boundary, setup, htmx, and composer", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /Ask about Danish language requirements/i })).toBeVisible();
  await expect(page.getByText("local-only answer path")).toBeVisible();
  await expect(page.getByText(/information assistant, not an authority or lawyer/i)).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
  await expect(page.getByRole("radio", { name: /Ollama/i })).toBeVisible();
  await expect(page.getByRole("radio", { name: /OpenAI-compatible local server/i })).toBeVisible();
  await expect(page.locator("form.setup-form")).toHaveAttribute("hx-target", "#setup-panel");
  await expect.poll(() => page.evaluate(() => Boolean(window.htmx))).toBe(true);
});

test("failed provider setup preserves non-secret values in the targeted setup panel", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill("http://127.0.0.1:1234");
  await page.getByRole("textbox", { name: "Generation model" }).fill("fail-model");
  await page.getByRole("button", { name: "Test and Save" }).click();

  const setupPanel = page.locator("#setup-panel");
  await expect(setupPanel).toContainText("Connection test failed");
  await expect(setupPanel).toContainText("Provider service is unreachable");
  await expect(page.getByRole("textbox", { name: "Endpoint" })).toHaveValue("http://127.0.0.1:1234");
  await expect(page.getByRole("textbox", { name: "Generation model" })).toHaveValue("fail-model");
  await expect(setupPanel).not.toContainText("secret");
});

test("successful provider setup shows active provider and survives page reload", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill("http://127.0.0.1:1234");
  await page.getByRole("textbox", { name: "Generation model" }).fill("browser-model");
  await page.getByRole("button", { name: "Test and Save" }).click();

  await expect(page.getByText("Provider verified")).toBeVisible();
  await expect(page.getByLabel("Runtime status")).toContainText("OpenAI-compatible local server - browser-model");
  await expect(page.getByText("browser-fixture")).toBeVisible();

  await page.reload();

  await expect(page.getByLabel("Runtime status")).toContainText("OpenAI-compatible local server - browser-model");
  await expect(page.getByText("browser-fixture")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("api_key");
  await expect(page.locator("body")).not.toContainText("secret");
});
