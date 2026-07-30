import { expect, test } from "@playwright/test";

const OPENAI_FIXTURE = {
  endpoint: "http://127.0.0.1:1234",
  model: "browser-model",
};

async function ensureBrowserProvider(page) {
  await page.goto("/");
  const runtimeStatus = page.getByLabel("Runtime status");
  if ((await runtimeStatus.textContent())?.includes(OPENAI_FIXTURE.model)) {
    return;
  }

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill(OPENAI_FIXTURE.endpoint);
  await page.getByRole("textbox", { name: "Generation model" }).fill(OPENAI_FIXTURE.model);
  await page.getByRole("button", { name: "Test and Save" }).click();
  await expect(runtimeStatus).toContainText(OPENAI_FIXTURE.model);
}

async function askGreeting(page) {
  await page.getByRole("textbox", { name: "Question" }).fill("hi");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".turn").last()).toBeVisible();
}

async function askSupportedQuestion(page) {
  await page
    .getByRole("textbox", { name: "Question" })
    .fill("What Danish test do I need for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".turn").last()).toBeVisible();
}

async function conversationLayout(page) {
  return page.evaluate(() => {
    const conversation = document.querySelector(".conversation");
    const turnStack = document.querySelector(".turn-stack");
    const composer = document.querySelector(".composer");
    const lastTurn = document.querySelector(".turn:last-child");
    if (
      !(conversation instanceof HTMLElement)
      || !(turnStack instanceof HTMLElement)
      || !(composer instanceof HTMLElement)
      || !(lastTurn instanceof HTMLElement)
    ) {
      return null;
    }

    const stackRect = turnStack.getBoundingClientRect();
    const composerRect = composer.getBoundingClientRect();
    const lastTurnRect = lastTurn.getBoundingClientRect();
    return {
      conversationBottom: conversation.getBoundingClientRect().bottom,
      stackBottom: stackRect.bottom,
      composerTop: composerRect.top,
      composerBottom: composerRect.bottom,
      lastTurnBottom: lastTurnRect.bottom,
      overflowY: getComputedStyle(turnStack).overflowY,
      composerPosition: getComputedStyle(composer).position,
      clientHeight: turnStack.clientHeight,
      scrollHeight: turnStack.scrollHeight,
      scrollTop: turnStack.scrollTop,
      viewportHeight: window.innerHeight,
    };
  });
}

test("Ollama setup discovers compatible installed generation models and submits the selection", async ({
  page,
}) => {
  await page.goto("/");

  await page.getByRole("radio", { name: /Ollama/i }).check();
  await page.getByRole("button", { name: "Find installed models" }).click();

  const model = page.getByRole("combobox", { name: "Generation model" });
  await expect(model).toBeVisible();
  await expect(model.locator("option")).toHaveText([
    "gemma4:12b",
    "gemma4:26b",
  ]);
  await expect(model.locator('option[value="embeddinggemma"]')).toHaveCount(0);
  await expect(model.locator('option[value$=":cloud"]')).toHaveCount(0);

  await model.selectOption("gemma4:26b");
  await page.getByRole("button", { name: "Test and Save" }).click();

  await expect(page.getByText("Provider verified")).toBeVisible();
  await expect(page.getByLabel("Runtime status")).toContainText("gemma4:26b");
});

test("model discovery failure stays in setup with the selected provider and endpoint", async ({
  page,
}) => {
  await page.goto("/");

  await page.getByRole("radio", { name: /Ollama/i }).check();
  await page
    .getByRole("textbox", { name: "Endpoint" })
    .fill("http://127.0.0.1:11435");
  await page.getByRole("button", { name: "Find installed models" }).click();

  const setupPanel = page.locator("#setup-panel");
  await expect(setupPanel.getByRole("alert")).toContainText(
    "Unable to discover local models",
  );
  await expect(page.getByRole("radio", { name: /Ollama/i })).toBeChecked();
  await expect(page.getByRole("textbox", { name: "Endpoint" })).toHaveValue(
    "http://127.0.0.1:11435",
  );
  await expect(page.locator("main.app-shell")).toBeVisible();
});

test("delayed provider setup shows visible pending state and prevents duplicate submission", async ({
  page,
}) => {
  let releaseSetup;
  let markSetupRequested;
  const setupRequested = new Promise((resolve) => {
    markSetupRequested = resolve;
  });
  const setupReleased = new Promise((resolve) => {
    releaseSetup = resolve;
  });
  await page.route("**/setup", async (route) => {
    markSetupRequested();
    await setupReleased;
    await route.continue();
  });
  await page.goto("/");

  await page.getByRole("radio", { name: /OpenAI-compatible local server/i }).check();
  await page.getByRole("textbox", { name: "Endpoint" }).fill(OPENAI_FIXTURE.endpoint);
  await page.getByRole("textbox", { name: "Generation model" }).fill(OPENAI_FIXTURE.model);
  const submit = page.getByRole("button", { name: "Test and Save" });
  const click = submit.click();
  await setupRequested;

  try {
    const setupPanel = page.locator("#setup-panel");
    await expect(
      setupPanel.getByRole("status").filter({ hasText: "Testing provider" }),
    ).toBeVisible({ timeout: 500 });
    await expect(submit).toBeDisabled();
  } finally {
    releaseSetup();
  }
  await click;

  await expect(page.getByText("Provider verified")).toBeVisible();
  await expect(page.getByLabel("Runtime status")).toContainText(OPENAI_FIXTURE.model);
});

test("a new answer intentionally reveals the latest turn without losing title focus", async ({
  page,
}) => {
  await page.setViewportSize({ width: 988, height: 1200 });
  await ensureBrowserProvider(page);

  for (let turn = 0; turn < 4; turn += 1) {
    await askSupportedQuestion(page);
  }

  const layout = await conversationLayout(page);
  expect(layout).not.toBeNull();
  expect(layout.overflowY).toBe("auto");
  expect(layout.scrollHeight).toBeGreaterThan(layout.clientHeight);
  expect(layout.scrollTop).toBeGreaterThan(0);
  expect(layout.lastTurnBottom).toBeLessThanOrEqual(layout.stackBottom + 1);
  await expect(page.getByRole("heading", { name: "Current Conversation" })).toBeFocused();
});

test("wide long conversation has a bounded scroller and visible non-overlapping composer", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1400, height: 800 });
  await ensureBrowserProvider(page);

  for (let turn = 0; turn < 4; turn += 1) {
    await askSupportedQuestion(page);
  }

  const layout = await conversationLayout(page);
  expect(layout).not.toBeNull();
  expect(layout.overflowY).toBe("auto");
  expect(layout.scrollHeight).toBeGreaterThan(layout.clientHeight);
  expect(layout.composerPosition).not.toBe("fixed");
  expect(layout.composerBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.stackBottom).toBeLessThanOrEqual(layout.composerTop + 1);
  expect(layout.lastTurnBottom).toBeLessThanOrEqual(layout.stackBottom + 1);
  expect(layout.conversationBottom).toBeLessThanOrEqual(layout.viewportHeight + 1);
});

test("invalid bulk deletion keeps the HTML app and valid deletion shows success", async ({
  page,
}) => {
  await ensureBrowserProvider(page);
  await askGreeting(page);
  await page.reload();

  const savedNavigation = page.getByRole("navigation", {
    name: "Saved conversations",
  });
  await expect(savedNavigation.getByRole("link", { name: "hi" }).first()).toBeVisible();

  await page
    .getByRole("textbox", {
      name: "Type DELETE ALL LOCAL CONVERSATIONS",
    })
    .fill("delete everything");
  await page.getByRole("button", { name: "Delete all local records" }).click();

  await expect(page.locator("main.app-shell")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "DELETE ALL LOCAL CONVERSATIONS",
  );
  await expect(savedNavigation.getByRole("link", { name: "hi" }).first()).toBeVisible();

  await page
    .getByRole("textbox", {
      name: "Type DELETE ALL LOCAL CONVERSATIONS",
    })
    .fill("DELETE ALL LOCAL CONVERSATIONS");
  await page.getByRole("button", { name: "Delete all local records" }).click();

  await expect(page.locator("main.app-shell")).toBeVisible();
  await expect(
    page.getByRole("status").filter({
      hasText: "All local conversation records deleted",
    }),
  ).toBeVisible();
  await expect(page.getByText("No conversation records yet.")).toBeVisible();
});

test("the first HTMX answer refreshes Local History without a reload", async ({
  page,
}) => {
  await ensureBrowserProvider(page);
  const historyLinks = page
    .getByRole("navigation", { name: "Saved conversations" })
    .locator(".history-list > li > a");
  const initialCount = await historyLinks.count();
  await askGreeting(page);

  await expect(historyLinks).toHaveCount(initialCount + 1);
});
