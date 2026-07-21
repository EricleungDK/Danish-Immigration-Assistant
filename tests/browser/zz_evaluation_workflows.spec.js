import { expect, test } from "@playwright/test";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

function recordObservations(...observationIds) {
  for (const observationId of observationIds) {
    test.info().annotations.push({
      type: "machine-observation",
      description: observationId,
    });
  }
}

test.beforeEach(async ({ request }) => {
  const response = await request.post("/__test__/reset-knowledge-release");
  expect(response.ok()).toBe(true);
  expect((await response.json()).knowledge_release_id).toBe("kr-2026-07-06.1");
});

test.afterEach(async ({ request }) => {
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
}

async function askSupportedQuestion(page) {
  await ensureBrowserProvider(page);
  await page
    .getByRole("textbox", { name: "Question" })
    .fill("What Danish test do I need for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeVisible();
}

async function currentConversationId(page) {
  const action = await page
    .locator('.conversation-actions form[action$="/export.json"]')
    .getAttribute("action");
  const conversationId = action?.match(/\/conversations\/([^/]+)\/export\.json$/)?.[1];
  expect(conversationId).toBeTruthy();
  return conversationId;
}

function expectOnlyLoopbackRequests(urls) {
  for (const value of urls) {
    const url = new URL(value);
    expect(LOOPBACK_HOSTS.has(url.hostname)).toBe(true);
    expect(value.toLowerCase()).not.toContain("secret");
    expect(value.toLowerCase()).not.toContain("api_key");
  }
}

function cssTimeInMilliseconds(value) {
  if (value.endsWith("ms")) return Number.parseFloat(value);
  if (value.endsWith("s")) return Number.parseFloat(value) * 1000;
  throw new Error(`Unsupported CSS time value: ${value}`);
}

test("eval-016-keyboard-evidence-drawer", async ({ page }) => {
  await page.goto("/");
  await askSupportedQuestion(page);

  const observedRequests = [];
  page.on("request", (request) => observedRequests.push(request.url()));
  const citation = page
    .getByRole("button", {
      name: /Inspect evidence: Permanent residence language requirements/i,
    })
    .first();
  await citation.focus();
  await page.keyboard.press("Enter");

  const drawer = page.getByRole("dialog", {
    name: "Permanent residence language requirements",
  });
  await expect(drawer).toBeVisible();
  await expect(drawer.locator("[id^='evidence-title-']")).toBeFocused();
  recordObservations("keyboard_controls_reachable");
  await expect(drawer.getByText("Publisher", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Official URL", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Checked", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Corpus", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Evidence Confidence", { exact: true })).toBeVisible();
  await expect(drawer.getByText(/Fresh Tomato Score:/).first()).toBeVisible();
  recordObservations(
    "assistive_provenance_and_trust_text",
    "evidence_confidence_text_visible",
    "fresh_tomato_text_visible",
  );

  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "Close evidence drawer" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(drawer).not.toBeVisible();
  await expect(citation).toBeFocused();
  recordObservations("dialog_focus_and_return", "no_unintended_focus_trap");

  const trustList = page.locator(".answer > .trust-list").first();
  await expect(trustList).toContainText("Evidence Confidence:");
  await expect(trustList).toContainText("Fresh Tomato Score:");
  recordObservations("trust_has_text_labels");
  expect(observedRequests).toEqual([]);
  recordObservations("drawer_open_has_no_request");
});

test("eval-017-responsive-reduced-motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 640, height: 900 });
  const observedRequests = [];
  page.on("request", (request) => observedRequests.push(request.url()));
  await page.goto("/");
  await askSupportedQuestion(page);
  await page.addStyleTag({ content: "html { font-size: 200% !important; }" });

  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Local tools" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New conversation" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Check for knowledge updates" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Provider setup" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Question" })).toBeVisible();
  await expect(page.locator(".answer > .trust-list").first()).toContainText(
    "Evidence Confidence:",
  );
  await expect(page.locator(".answer > .trust-list").first()).toContainText(
    "Fresh Tomato Score:",
  );
  recordObservations(
    "narrow_core_controls_visible",
    "evidence_confidence_text_visible",
    "fresh_tomato_text_visible",
  );

  const horizontalOverflow = await page.evaluate(() => {
    const root = document.scrollingElement ?? document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
  expect(horizontalOverflow).toBe(false);

  const citation = page
    .getByRole("button", {
      name: /Inspect evidence: Permanent residence language requirements/i,
    })
    .first();
  await citation.click();
  const drawer = page.getByRole("dialog", {
    name: "Permanent residence language requirements",
  });
  await expect(drawer).toBeVisible();
  const drawerState = await page.locator(".evidence-drawer").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      horizontalOverflow: element.scrollWidth > element.clientWidth + 1,
      animationDuration: style.animationDuration,
      transitionDuration: style.transitionDuration,
    };
  });
  expect(drawerState.horizontalOverflow).toBe(false);
  recordObservations("two_hundred_percent_no_horizontal_overflow");
  expect(cssTimeInMilliseconds(drawerState.animationDuration)).toBeLessThanOrEqual(0.01);
  expect(cssTimeInMilliseconds(drawerState.transitionDuration)).toBeLessThanOrEqual(0.01);
  await page.keyboard.press("Escape");
  await expect(citation).toBeFocused();
  await expect(page.locator("#interaction-status")).toHaveText("Conversation updated.");
  recordObservations(
    "narrow_core_workflow_usable",
    "reduced_motion_preserves_status",
    "trust_and_status_use_text",
  );
  expectOnlyLoopbackRequests(observedRequests);
  recordObservations("responsive_requests_are_loopback_only");
});

test("eval-015-update-telemetry-privacy", async ({ page }) => {
  await page.goto("/");
  await askSupportedQuestion(page);
  const observedRequests = [];
  page.on("request", (request) => observedRequests.push(request.url()));
  const localTools = page.getByRole("complementary", { name: "Local tools" });
  const corpus = localTools.locator("section").filter({
    has: page.getByRole("heading", { name: "Corpus" }),
  });
  await expect(corpus.locator(".runtime-list").first()).toContainText("kr-2026-07-06.1");
  recordObservations("active_corpus_identity_visible");

  await localTools.getByRole("button", { name: "Check for knowledge updates" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Knowledge update metadata available" })).toBeVisible();
  await expect(page.getByText("No release archive has been downloaded")).toBeVisible();
  await expect(page.getByRole("button", { name: "Install reviewed release" })).toHaveCount(0);
  await expect(corpus.locator(".runtime-list").first()).toContainText("kr-2026-07-06.1");
  recordObservations("availability_without_install", "no_automatic_install");

  await page.getByRole("button", { name: "Download and verify signed release" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: "Signed knowledge update ready to review" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Install reviewed release" })).toBeVisible();
  await expect(corpus.locator(".runtime-list").first()).toContainText("kr-2026-07-06.1");
  recordObservations("separate_download_and_install_approval");
  expectOnlyLoopbackRequests(observedRequests);

  await page.getByRole("button", { name: "Dismiss" }).click();
  await page.waitForLoadState("networkidle");
});

test("eval-019-runtime-identity-visible", async ({ page }) => {
  const browserLogs = [];
  page.on("console", (message) => browserLogs.push(message.text()));
  page.on("pageerror", (error) => browserLogs.push(error.message));
  await page.goto("/");
  await askSupportedQuestion(page);
  const conversationId = await currentConversationId(page);
  const originalAnswer = page.locator(".answer").first();
  await expect(originalAnswer.locator(".answer-meta")).toContainText(
    "Provider: openai_compatible",
  );
  await expect(originalAnswer.locator(".answer-meta")).toContainText("Model: browser-model");
  await expect(originalAnswer.locator(".answer-meta")).toContainText(
    "Corpus: kr-2026-07-06.1",
  );
  await expect(originalAnswer).toContainText("Checked: 2026-06-15");
  recordObservations(
    "provider_identity_visible",
    "model_identity_visible",
    "provider_model_corpus_check_date_visible",
  );

  const originalIdentityText = await originalAnswer.locator(".answer-meta").textContent();
  expect(originalIdentityText).not.toContain("127.0.0.1:1234");
  expect(originalIdentityText).not.toContain("What Danish test");
  expect(originalIdentityText?.toLowerCase()).not.toContain("api_key");
  recordObservations("identity_display_excludes_secrets");

  const citation = originalAnswer
    .getByRole("button", {
      name: /Inspect evidence: Permanent residence language requirements/i,
    })
    .first();
  await citation.click();
  const drawer = page.getByRole("dialog", {
    name: "Permanent residence language requirements",
  });
  await expect(drawer.getByText("Publisher", { exact: true })).toBeVisible();
  await expect(drawer.getByText("SIRI", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Model", { exact: true })).toBeVisible();
  await expect(drawer.getByText("browser-model", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close evidence drawer" }).click();
  recordObservations("model_and_source_labels_distinct");

  const observedRequests = [];
  page.on("request", (request) => observedRequests.push(request.url()));
  const localTools = page.getByRole("complementary", { name: "Local tools" });
  await localTools.getByRole("button", { name: "Check for knowledge updates" }).click();
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Download and verify signed release" }).click();
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: "Install reviewed release" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Active corpus: kr-2026-07-07.1")).toBeVisible();

  await page.goto(`/conversations/${conversationId}`);
  const historicalAnswer = page.locator(".answer").first();
  await expect(historicalAnswer.locator(".answer-meta")).toContainText("browser-model");
  await expect(historicalAnswer.locator(".answer-meta")).toContainText(
    "Corpus: kr-2026-07-06.1",
  );
  await expect(page.getByRole("complementary", { name: "Local tools" })).toContainText(
    "kr-2026-07-07.1",
  );
  recordObservations(
    "corpus_identity_visible",
    "historical_provenance_unchanged_after_update",
  );
  await expect(page.locator("body")).not.toContainText("api_key");
  await expect(page.locator("body")).not.toContainText("secret");
  expectOnlyLoopbackRequests(observedRequests);
  for (const logLine of browserLogs) {
    const lowered = logLine.toLowerCase();
    expect(lowered).not.toContain("api_key");
    expect(lowered).not.toContain("bearer ");
    expect(lowered).not.toContain("password=");
    expect(lowered).not.toContain("what danish test do i need");
  }
  recordObservations("identity_urls_and_ui_exclude_secrets");
});
