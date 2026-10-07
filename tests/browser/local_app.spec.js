import { expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  const response = await request.post("/__test__/reset-knowledge-release");
  expect(response.ok()).toBe(true);
});

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

test("snapshot label states the release date, not kept current, and not legal advice", async ({ page }) => {
  await page.goto("/");

  const banner = page.getByRole("note", { name: "Knowledge snapshot notice" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Knowledge snapshot from 2026-07-06.");
  await expect(banner).toContainText("Not kept current.");
  await expect(banner).toContainText("not legal advice");

  await ensureBrowserProvider(page);
  await page.getByRole("textbox", { name: "Question" }).fill("What Danish test do I need for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();

  await expect(banner).toContainText("Knowledge snapshot from 2026-07-06.");
  const compactTrust = page.locator(".answer > .trust-list").first();
  await expect(compactTrust.getByText("Fresh Tomato Score: High")).toBeVisible();
  await expect(compactTrust).toContainText(
    "Freshness is judged as of the snapshot date (2026-07-06), not today.",
  );
  await page.getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i }).first().click();
  const drawer = page.getByRole("dialog", { name: "Permanent residence language requirements" });
  await expect(drawer).toContainText("Freshness is judged as of the snapshot date (2026-07-06), not today.");
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

test("greeting stays conversational without immigration evidence", async ({ page }) => {
  await page.goto("/");
  await ensureBrowserProvider(page);

  await page.getByRole("textbox", { name: "Question" }).fill("hi");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(
    page.getByRole("heading", { name: "Conversation", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Hello! I can chat briefly/i)).toBeVisible();
  await expect(
    page.getByText("Handled locally without retrieval or model generation"),
  ).toBeVisible();
  await expect(page.getByText("Official fact", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Evidence Confidence:/)).toHaveCount(0);
});

test("composer does not overlap answer region from 681 through 1080 pixels", async ({ page }) => {
  await page.setViewportSize({ width: 1080, height: 800 });
  await page.goto("/");
  await ensureBrowserProvider(page);

  await page.getByRole("textbox", { name: "Question" }).fill(
    "What Danish test do I need for permanent residence?",
  );
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();

  for (const width of [681, 820, 1080]) {
    await page.setViewportSize({ width, height: 800 });

    const layout = await page.evaluate(() => {
      const turnStack = document.querySelector(".turn-stack");
      const composer = document.querySelector(".composer");
      if (!(turnStack instanceof HTMLElement) || !(composer instanceof HTMLElement)) {
        return null;
      }
      const answerRegion = turnStack.getBoundingClientRect();
      const composerRegion = composer.getBoundingClientRect();
      return {
        answerBottom: answerRegion.bottom,
        composerTop: composerRegion.top,
        composerPosition: getComputedStyle(composer).position,
      };
    });

    expect(layout, `layout regions should exist at ${width}px`).not.toBeNull();
    expect(layout.composerPosition, `composer positioning at ${width}px`).not.toBe("fixed");
    expect(
      layout.answerBottom,
      `answer region should end before composer at ${width}px`,
    ).toBeLessThanOrEqual(layout.composerTop + 1);
  }
});

async function chromeLayout(page) {
  return page.evaluate(() => {
    const box = (selector) => document.querySelector(selector).getBoundingClientRect();
    const composer = box(".composer");
    return {
      composerTop: composer.top,
      composerBottom: composer.bottom,
      composerLeft: composer.left,
      composerRight: composer.right,
      viewportHeight: window.innerHeight,
      viewportWidth: window.innerWidth,
      bannerHeight: box(".snapshot-banner").height,
      chromeProperty: parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--chrome-height")),
      chromeActual: box(".topbar").height + box(".snapshot-banner").height,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    };
  });
}

for (const viewport of [
  { width: 360, height: 740 },
  { width: 390, height: 844 },
]) {
  test(`wrapped snapshot banner keeps the composer reachable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await expect(page.getByRole("note", { name: "Knowledge snapshot notice" })).toBeVisible();

    const layout = await chromeLayout(page);
    // The banner really wraps at this width, so no fixed height could account for it.
    expect(layout.bannerHeight).toBeGreaterThan(40);
    expect(layout.chromeProperty).toBeCloseTo(layout.chromeActual, 0);
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);

    // Narrow layouts stack the panels and scroll the page: the composer is reachable.
    await page.locator(".composer").scrollIntoViewIfNeeded();
    const reached = await chromeLayout(page);
    expect(reached.composerTop).toBeGreaterThanOrEqual(0);
    expect(reached.composerBottom).toBeLessThanOrEqual(reached.viewportHeight + 1);
    expect(reached.composerLeft).toBeGreaterThanOrEqual(0);
    expect(reached.composerRight).toBeLessThanOrEqual(reached.viewportWidth + 1);
    await expect(page.getByRole("textbox", { name: "Question" })).toBeInViewport();
    await expect(page.getByRole("button", { name: "Send" })).toBeInViewport();
    expect((await chromeLayout(page)).scrollWidth).toBeLessThanOrEqual(reached.clientWidth);
  });
}

for (const viewport of [
  { width: 681, height: 800 },
  { width: 820, height: 800 },
  { width: 1080, height: 800 },
]) {
  test(`snapshot banner leaves the composer on the first screen at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await expect(page.getByRole("note", { name: "Knowledge snapshot notice" })).toBeVisible();

    const layout = await chromeLayout(page);
    expect(layout.chromeProperty).toBeCloseTo(layout.chromeActual, 0);
    expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  });
}

test("desktop layout height uses the measured banner height", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await expect(page.getByRole("note", { name: "Knowledge snapshot notice" })).toBeVisible();
  const layout = await chromeLayout(page);
  expect(layout.chromeProperty).toBeCloseTo(layout.chromeActual, 0);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  const shell = await page.locator(".app-shell").evaluate((el) => el.getBoundingClientRect().height);
  expect(shell).toBeCloseTo(layout.viewportHeight - layout.chromeProperty, 0);
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

  await page.evaluate(() => { window.downloadReviewPageMarker = true; });
  await page.getByRole("button", { name: "Download and verify signed release" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toBeVisible();
  expect(await page.evaluate(() => window.downloadReviewPageMarker)).toBe(true);
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toBeFocused();
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
  await page.reload();
  await expect(corpusSection.locator(".runtime-list").first()).toContainText("kr-2026-07-07.1");
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toHaveCount(0);
});
