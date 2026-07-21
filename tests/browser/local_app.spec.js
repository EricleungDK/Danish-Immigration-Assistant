import { expect, test } from "@playwright/test";

async function ensureBrowserProvider(page) {
  const runtimeStatus = page.getByLabel("Runtime status");
  if ((await runtimeStatus.textContent())?.includes("browser-model")) {
    return;
  }

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill("http://127.0.0.1:1234");
  await page.getByRole("textbox", { name: "Generation model" }).fill("browser-model");
  await page.getByRole("button", { name: "Test and Save" }).click();
  await page.waitForLoadState("networkidle");
  await expect(runtimeStatus).toContainText("OpenAI-compatible local server - browser-model");
  await expect(page.getByText("Provider verified")).toBeVisible();
}

async function currentConversationId(page) {
  const exportAction = await page
    .locator('.conversation-actions form[action$="/export.json"]')
    .getAttribute("action");
  const conversationId = exportAction?.match(/\/conversations\/([^/]+)\/export\.json$/)?.[1];
  expect(conversationId).toBeTruthy();
  return conversationId;
}

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
  await expect(
    page.getByRole("heading", { name: "Automatic release metadata check complete" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Knowledge update metadata available" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Install reviewed release" })).toHaveCount(0);
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

test("supported question produces cited answer and persists across reload", async ({ page }) => {
  await page.goto("/");

  await ensureBrowserProvider(page);

  const question = page.getByRole("textbox", { name: "Question" });
  await expect(question).toBeVisible();
  await question.fill("What Danish test do I need for permanent residence?");
  await expect(question).toHaveValue("What Danish test do I need for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();
  const answerSections = page.locator(".answer-sections").first();
  await expect(answerSections.getByText("Official fact", { exact: true })).toBeVisible();
  await expect(answerSections.getByText("Interpretation", { exact: true })).toBeVisible();
  await expect(page.getByText("Prøve i Dansk 2").first()).toBeVisible();
  await expect(page.getByText("Permanent residence language requirements").first()).toBeVisible();
  await expect(page.getByText("Checked: 2026-06-15").first()).toBeVisible();
  await expect(page.getByText("Corpus: kr-2026-07-06.1").first()).toBeVisible();
  const compactTrust = page.locator(".answer > .trust-list").first();
  await expect(compactTrust.getByText("Evidence Confidence: High")).toBeVisible();
  await expect(compactTrust.getByText("Fresh Tomato Score: High")).toBeVisible();

  await page.reload();
  await page.getByRole("link", { name: "What Danish test do I need for permanent residence?" }).first().click();

  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();
  await expect(page.getByText("Prøve i Dansk 2").first()).toBeVisible();
  await expect(page.getByText("Corpus: kr-2026-07-06.1").first()).toBeVisible();
});

test("new conversation resets the composer without deleting saved history", async ({ page }) => {
  await page.goto("/");

  await ensureBrowserProvider(page);

  const questionText = `What Danish test do I need for permanent residence? new thread ${Date.now()}`;
  await page.getByRole("textbox", { name: "Question" }).fill(questionText);
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();
  await expect(page.getByText(questionText)).toBeVisible();
  const conversationId = await currentConversationId(page);

  await page.getByRole("link", { name: "New conversation" }).click();

  await expect(page.getByRole("heading", { name: /Ask about Danish language requirements/i })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Question" })).toHaveValue("");
  await expect(page.locator("#conversation-main input[name='conversation_id']")).toHaveCount(0);

  const savedLink = page
    .getByRole("navigation", { name: "Saved conversations" })
    .locator(`a[href="/conversations/${conversationId}"]`);
  await expect(savedLink).toBeVisible();
  await savedLink.click();
  await expect(page.getByText(questionText)).toBeVisible();
});

test("inline citation opens accessible evidence drawer with preserved trust state", async ({ page }) => {
  await page.goto("/");

  await ensureBrowserProvider(page);

  const question = page.getByRole("textbox", { name: "Question" });
  await expect(question).toBeVisible();
  await question.fill("What Danish test do I need for permanent residence?");
  await expect(question).toHaveValue("What Danish test do I need for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();
  const citation = page.getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i }).first();
  await citation.click();

  const drawer = page.getByRole("dialog", { name: "Permanent residence language requirements" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("Claim Support");
  await expect(drawer).toContainText("Evidence Confidence: High");
  await expect(drawer).toContainText("Fresh Tomato Score: High");
  await expect(drawer).toContainText("browser-model");
  await expect(drawer).toContainText("kr-2026-07-06.1");
  await expect(drawer).toContainText("https://www.nyidanmark.dk/da/Du-vil-ansoege/Permanent-ophold");
  await expect(page.locator(".turn-question").getByText("What Danish test do I need for permanent residence?")).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id.startsWith("evidence-title-"))).toBe(true);

  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await expect(citation).toBeFocused();

  await page.reload();
  await page.getByRole("link", { name: "What Danish test do I need for permanent residence?" }).first().click();
  const persistedCitation = page.getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i }).first();
  await persistedCitation.press("Enter");

  const persistedDrawer = page.getByRole("dialog", { name: "Permanent residence language requirements" });
  await expect(persistedDrawer).toBeVisible();
  await expect(persistedDrawer).toContainText("Evidence Confidence: High");
  await expect(persistedDrawer).toContainText("Fresh Tomato Score: High");
  await expect(persistedDrawer).toContainText("browser-model");
});

test("GitHub knowledge update requires separate download review and install actions", async ({ page }) => {
  await page.goto("/");

  const corpusPanel = page.getByRole("complementary", { name: "Local tools" });
  const corpusSection = corpusPanel.locator("section").filter({
    has: page.getByRole("heading", { name: "Corpus" }),
  });
  await expect(corpusSection.locator(".runtime-list").first()).toContainText("kr-2026-07-06.1");

  await expect(page.getByRole("heading", { name: "Knowledge update metadata available" })).toBeVisible();
  await expect(page.getByText("kr-2026-07-07.1", { exact: true })).toBeVisible();
  await expect(page.getByText("No release archive has been downloaded")).toBeVisible();
  await expect(page.getByRole("button", { name: "Install reviewed release" })).toHaveCount(0);

  await page.getByRole("button", { name: "Dismiss" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Knowledge update metadata available" })).toHaveCount(0);
  await expect(corpusSection.locator(".runtime-list").first()).toContainText("kr-2026-07-06.1");

  await corpusPanel.getByRole("button", { name: "Check for knowledge updates" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Knowledge update metadata available" })).toBeVisible();

  await page.getByRole("button", { name: "Download and verify signed release" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toBeVisible();
  await expect(page.getByText("Signed manifest verified")).toBeVisible();
  await expect(corpusSection.locator(".runtime-list").first()).toContainText("kr-2026-07-06.1");

  await page.getByRole("button", { name: "Install reviewed release" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Knowledge update installed" })).toBeVisible();
  await expect(page.getByText("Active corpus: kr-2026-07-07.1")).toBeVisible();
  const installStatus = page.locator("#knowledge-installation-status");
  await expect(installStatus).toHaveAttribute("role", "status");
  await expect(installStatus.locator("progress")).toHaveAttribute("value", "100");
  for (const phase of [
    "verification",
    "extraction",
    "indexing",
    "embedding",
    "compatibility",
    "activation",
    "complete",
  ]) {
    await expect(installStatus.locator(`[data-install-phase="${phase}"]`)).toBeVisible();
  }
  await expect(corpusSection.locator(".runtime-list").first()).toContainText("kr-2026-07-07.1");
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toHaveCount(0);
});
