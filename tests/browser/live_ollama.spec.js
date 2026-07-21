import { expect, test } from "@playwright/test";

test("production browser journey uses live Ollama and the active signed corpus", async ({
  page,
}) => {
  test.skip(
    process.env.DI_RAG_RUN_LIVE_BROWSER !== "1",
    "Set DI_RAG_RUN_LIVE_BROWSER=1 and target a configured production app process.",
  );
  test.setTimeout(180_000);

  const browserRequests = [];
  page.on("request", (request) => browserRequests.push(request.url()));
  await page.goto("/");

  await expect(page.getByLabel("Runtime status")).toContainText("Ollama - gemma4:12b");
  const localTools = page.getByRole("complementary", { name: "Local tools" });
  await expect(localTools).toContainText("kr-2026-07-06.1");
  await expect(localTools).toContainText("embeddinggemma");

  await page
    .getByRole("textbox", { name: "Question" })
    .fill("Which Danish language test is documented for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();

  const conversation = page.getByRole("heading", { name: "Current Conversation" });
  await expect(conversation).toBeVisible({ timeout: 120_000 });
  const answer = page.locator(".answer").first();
  await expect(answer).toContainText("Prøve i Dansk 2");
  await expect(answer).toContainText("Evidence Confidence:");
  await expect(answer).toContainText("Fresh Tomato Score:");
  await expect(answer.locator(".answer-meta")).toContainText("Provider: ollama");
  await expect(answer.locator(".answer-meta")).toContainText("Model: gemma4:12b");
  await expect(answer.locator(".answer-meta")).toContainText(
    "Corpus: kr-2026-07-06.1",
  );
  await expect(
    page.getByRole("button", { name: /Inspect evidence:/ }).first(),
  ).toBeVisible();

  await page.getByRole("textbox", { name: "Question" }).fill("What about PD3?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".turn")).toHaveCount(2, { timeout: 120_000 });
  const followUpAnswer = page.locator(".answer").last();
  await expect(followUpAnswer).toContainText("Prøve i Dansk 3");
  await expect(followUpAnswer.locator(".answer-meta")).toContainText(
    "Model: gemma4:12b",
  );
  await expect(followUpAnswer.locator(".answer-meta")).toContainText(
    "Corpus: kr-2026-07-06.1",
  );

  for (const value of browserRequests) {
    const url = new URL(value);
    expect(new Set(["127.0.0.1", "localhost", "[::1]"]).has(url.hostname)).toBe(true);
  }
});
