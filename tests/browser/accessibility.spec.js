import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const BROWSER_PROVIDER = {
  endpoint: "http://127.0.0.1:1234",
  model: "browser-model",
  runtimeStatus: "OpenAI-compatible local server - browser-model",
};

async function replaceInputValue(page, locator, value) {
  await locator.focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText(value);
}

async function ensureBrowserProvider(page) {
  const runtimeStatus = page.getByLabel("Runtime status");
  if ((await runtimeStatus.textContent())?.includes(BROWSER_PROVIDER.model)) {
    return;
  }

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill(BROWSER_PROVIDER.endpoint);
  await page.getByRole("textbox", { name: "Generation model" }).fill(BROWSER_PROVIDER.model);
  await page.getByRole("button", { name: "Test and Save" }).click();
  await page.waitForLoadState("networkidle");
  await expect(runtimeStatus).toContainText(BROWSER_PROVIDER.runtimeStatus);
}

async function askQuestion(page, questionText) {
  await ensureBrowserProvider(page);
  await page
    .getByRole("textbox", { name: "Question" })
    .fill(questionText);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();
}

async function currentConversationId(page) {
  const exportAction = await page
    .locator('.conversation-actions form[action$="/export.json"]')
    .getAttribute("action");
  const conversationId = exportAction?.match(/\/conversations\/([^/]+)\/export\.json$/)?.[1];
  expect(conversationId).toBeTruthy();
  return conversationId;
}

async function expectNoSeriousOrCriticalViolations(page) {
  const results = await new AxeBuilder({ page }).analyze();
  const blockingViolations = results.violations.filter((violation) =>
    ["critical", "serious"].includes(violation.impact),
  );

  expect(blockingViolations).toEqual([]);
}

function cssTimeInMilliseconds(value) {
  if (value.endsWith("ms")) {
    return Number.parseFloat(value);
  }
  if (value.endsWith("s")) {
    return Number.parseFloat(value) * 1000;
  }
  throw new Error(`Unsupported CSS time value: ${value}`);
}

test("core journeys have no critical or serious automated accessibility violations", async ({
  page,
}) => {
  await page.goto("/");
  await expectNoSeriousOrCriticalViolations(page);

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill(BROWSER_PROVIDER.endpoint);
  await page.getByRole("textbox", { name: "Generation model" }).fill("fail-model");
  await page.getByRole("button", { name: "Test and Save" }).click();
  await expect(page.getByRole("alert")).toContainText("Connection test failed");
  await expectNoSeriousOrCriticalViolations(page);

  await askQuestion(page, "What Danish test do I need for permanent residence?");
  await expectNoSeriousOrCriticalViolations(page);

  await page.reload();
  await expect(page.getByRole("navigation", { name: "Saved conversations" })).toBeVisible();
  await expectNoSeriousOrCriticalViolations(page);

  await page.getByRole("link", { name: /What Danish test do I need/i }).first().click();
  await page
    .getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i })
    .first()
    .click();
  await expect(
    page.getByRole("dialog", { name: "Permanent residence language requirements" }),
  ).toBeVisible();
  await expectNoSeriousOrCriticalViolations(page);

  await page.getByRole("button", { name: "Close evidence drawer" }).click();
  await page.getByRole("button", { name: "Check for knowledge updates" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Knowledge update metadata available" })).toBeVisible();
  await expectNoSeriousOrCriticalViolations(page);
});

test("setup and conversation updates provide keyboard flow, visible focus, and status announcements", async ({
  page,
}) => {
  await page.goto("/");

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).focus();
  await page.keyboard.press("Space");
  await expect(page.getByRole("radio", { name: /OpenAI-compatible local server/i })).toBeChecked();
  await replaceInputValue(page, page.getByRole("textbox", { name: "Endpoint" }), BROWSER_PROVIDER.endpoint);
  await replaceInputValue(page, page.getByRole("textbox", { name: "Generation model" }), "fail-model");
  await page.getByRole("button", { name: "Test and Save" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toContainText("Connection test failed");
  await expect(page.locator("#interaction-status")).toHaveText("Provider setup updated.");

  await replaceInputValue(page, page.getByRole("textbox", { name: "Generation model" }), BROWSER_PROVIDER.model);
  await page.getByRole("button", { name: "Test and Save" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForLoadState("networkidle");
  await expect(page.getByLabel("Runtime status")).toContainText(BROWSER_PROVIDER.runtimeStatus);

  await replaceInputValue(
    page,
    page.getByRole("textbox", { name: "Question" }),
    "What Danish test do I need for permanent residence?",
  );
  await page.getByRole("button", { name: "Send" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeFocused();
  await expect(page.locator("#interaction-status")).toHaveText("Conversation updated.");
});

test("evidence drawer supports keyboard-only modal focus management", async ({ page }) => {
  await page.goto("/");
  await askQuestion(page, "What Danish test do I need for permanent residence?");

  const citation = page
    .getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i })
    .first();
  await citation.focus();
  await page.keyboard.press("Enter");

  const drawer = page.getByRole("dialog", { name: "Permanent residence language requirements" });
  await expect(drawer).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id.startsWith("evidence-title-"))).toBe(true);
  await expect(drawer.locator("[id^='evidence-title-']")).toBeFocused();
  await expect(drawer.locator("[id^='evidence-title-']")).toHaveCSS("outline-style", "solid");

  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "Close evidence drawer" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(drawer).not.toBeVisible();
  await expect(citation).toBeFocused();

  await citation.press("Enter");
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await expect(citation).toBeFocused();
});

test("history, export, deletion, and update journeys complete from the keyboard", async ({
  page,
}) => {
  const questionText = `What Danish test do I need for permanent residence? keyboard record ${Date.now()}`;
  await page.goto("/");
  await askQuestion(page, questionText);
  const conversationId = await currentConversationId(page);
  await page.reload();

  const localTools = page.getByRole("complementary", { name: "Local tools" });
  const historyItem = localTools.locator(
    `.history-list li:has(a[href="/conversations/${conversationId}"])`,
  );
  const exportDownload = page.waitForEvent("download");
  await historyItem.getByRole("link", { name: "Export" }).focus();
  await expect(historyItem.getByRole("link", { name: "Export" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect((await exportDownload).suggestedFilename()).toContain("danish-rag-conversation-");

  await localTools.getByRole("button", { name: "Check for knowledge updates" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Knowledge update metadata available" })).toBeVisible();

  await page.getByRole("button", { name: "Download and verify signed release" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toBeVisible();
  await expectNoSeriousOrCriticalViolations(page);

  await page.getByRole("button", { name: "Dismiss" }).click();
  await page.waitForLoadState("networkidle");

  await historyItem.getByRole("button", { name: "Delete" }).focus();
  await page.keyboard.press("Enter");
  await page.waitForLoadState("networkidle");
  await expect(localTools.getByText(questionText)).not.toBeVisible();
});

test("keyboard users can skip directly to the conversation", async ({ page }) => {
  await page.goto("/");

  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to conversation" });
  await expect(skipLink).toBeFocused();

  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /Ask about Danish language requirements/i })).toBeFocused();
});

test("200 percent reflow and narrow viewports avoid two-dimensional page scrolling", async ({
  page,
}) => {
  for (const scenario of [
    { viewport: { width: 640, height: 900 }, textScalePercent: 200 },
    { viewport: { width: 390, height: 900 }, textScalePercent: 100 },
  ]) {
    await page.setViewportSize(scenario.viewport);
    await page.goto("/");
    await askQuestion(page, "What Danish test do I need for permanent residence?");
    const conversationId = await currentConversationId(page);
    await page.reload();
    await page.goto(`/conversations/${conversationId}`);
    await page.addStyleTag({
      content: `html { font-size: ${scenario.textScalePercent}% !important; }`,
    });

    await expect(page.getByRole("main")).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Local tools" })).toBeVisible();
    await expect(page.getByRole("link", { name: "New conversation" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Check for knowledge updates" })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Provider setup" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();

    const pageHasHorizontalOverflow = await page.evaluate(() => {
      const root = document.scrollingElement ?? document.documentElement;
      return root.scrollWidth > root.clientWidth + 1;
    });
    expect(pageHasHorizontalOverflow).toBe(false);

    await page
      .getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i })
      .first()
      .click();
    await expect(page.getByRole("dialog", { name: "Permanent residence language requirements" })).toBeVisible();
    const drawerHasHorizontalOverflow = await page.evaluate(() => {
      const drawer = document.querySelector(".evidence-drawer");
      if (!(drawer instanceof HTMLElement)) {
        return true;
      }
      return drawer.scrollWidth > drawer.clientWidth + 1;
    });
    expect(drawerHasHorizontalOverflow).toBe(false);
    await page.keyboard.press("Escape");
  }
});

test("reduced motion removes nonessential transitions while preserving status feedback", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");

  await askQuestion(page, "What Danish test do I need for permanent residence?");
  const evidenceButton = page
    .getByRole("button", { name: /Inspect evidence: Permanent residence language requirements/i })
    .first();
  await evidenceButton.click();

  const motion = await page.locator(".evidence-drawer").evaluate((drawer) => {
    const style = getComputedStyle(drawer);
    return {
      animationDuration: style.animationDuration,
      transitionDuration: style.transitionDuration,
    };
  });
  expect(cssTimeInMilliseconds(motion.animationDuration)).toBeLessThanOrEqual(0.01);
  expect(cssTimeInMilliseconds(motion.transitionDuration)).toBeLessThanOrEqual(0.01);
  await expect(page.getByRole("dialog", { name: "Permanent residence language requirements" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(evidenceButton).toBeFocused();
});

test("trust, warning, and refusal indicators are distinguishable without color", async ({
  page,
}) => {
  await page.goto("/");
  await askQuestion(page, "What Danish test do I need for permanent residence? warning");

  const officialFact = page.locator(".answer-section-official_fact").first();
  const interpretation = page.locator(".answer-section-interpretation").first();
  const sourceWarning = page.locator(".answer-section-source_warning").first();
  await expect(officialFact.getByText("Official fact")).toBeVisible();
  await expect(interpretation.getByText("Interpretation")).toBeVisible();
  await expect(sourceWarning.getByText("Source warning")).toBeVisible();
  await expect(sourceWarning).toHaveCSS("border-left-style", "double");
  await expect(interpretation).toHaveCSS("border-left-style", "dashed");
  await expect(page.locator(".trust-list").first()).toContainText("Evidence Confidence: High");
  await expect(page.locator(".trust-list").first()).toContainText("Fresh Tomato Score: High");

  await askQuestion(
    page,
    "I passed PD2, have lived in Denmark for 7 years, and have a job. Do I qualify for permanent residence?",
  );
  const refusal = page.locator(".answer-section-refusal").first();
  await expect(refusal.getByText("Evidence-bounded refusal")).toBeVisible();
  await expect(refusal).toHaveCSS("border-top-style", "solid");
});


test("manual update check announces its result without navigating or losing the draft", async ({ page }) => {
  await page.goto("/");
  await ensureBrowserProvider(page);
  await page.getByRole("textbox", { name: "Question" }).fill("Unsubmitted accessibility test");
  await page.evaluate(() => {
    window.updateCheckPageMarker = true;
    window.updateCheckOriginalButton = document.querySelector(".update-check-form button");
    window.updateCheckButtonRemoved = false;
    new MutationObserver(() => {
      if (!window.updateCheckOriginalButton.isConnected) window.updateCheckButtonRemoved = true;
    }).observe(document.body, { childList: true, subtree: true });
  });
  const button = page.getByRole("button", { name: "Check for knowledge updates" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#knowledge-check-announcement")).toContainText("Knowledge update available");
  expect(await page.evaluate(() => window.updateCheckOriginalButton === document.querySelector(".update-check-form button"))).toBe(true);
  expect(await page.evaluate(() => window.updateCheckButtonRemoved)).toBe(false);
  expect(await page.evaluate(() => window.updateCheckPageMarker)).toBe(true);
  await expect(page.getByRole("textbox", { name: "Question" })).toHaveValue("Unsubmitted accessibility test");
  await expect(page.getByRole("button", { name: "Check for knowledge updates" })).toBeFocused();
});


test("manual update network failure is announced beside the retained button", async ({ page }) => {
  await page.goto("/");
  await page.route("**/knowledge-updates/check", route => route.abort());
  const button = page.getByRole("button", { name: "Check for knowledge updates" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#knowledge-check-announcement")).toContainText("Knowledge update check failed");
  await expect(button).toBeFocused();
  await expect(page.locator(".update-check-form")).not.toHaveAttribute("aria-busy", "true");
});


test("download failure keeps review controls and announces the server error locally", async ({ page }) => {
  await page.goto("/");
  const download = page.getByRole("button", { name: "Download and verify signed release" });
  await expect(download).toBeVisible();
  await page.route("**/knowledge-updates/download", route => route.fulfill({
    status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Signature verification failed. The active release is unchanged." }),
  }));
  await download.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#knowledge-check-announcement")).toContainText("Signature verification failed");
  await expect(download).toBeFocused();
  await expect(page.locator(".update-download-form")).not.toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("button", { name: "Install reviewed release" })).toHaveCount(0);
});
