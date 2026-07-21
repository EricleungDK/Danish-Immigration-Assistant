import { chromium } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";

const args = parseArguments(process.argv.slice(2));
const required = [
  "phase",
  "base-url",
  "provider-endpoint",
  "generation-model",
  "target-release-id",
  "state-path",
  "output-path",
];
for (const name of required) {
  if (!args[name]) throw new Error(`Missing --${name}`);
}
if (!new Set(["before-restart", "after-restart"]).has(args.phase)) {
  throw new Error("Unsupported browser phase");
}

const baseURL = new URL(args["base-url"]);
if (baseURL.protocol !== "http:" || baseURL.hostname !== "127.0.0.1") {
  throw new Error("Browser monitor requires an IPv4 loopback base URL");
}

const browser = await chromium.launch({ headless: true });
let result;
try {
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(180_000);
  page.setDefaultNavigationTimeout(180_000);
  const observedRequests = [];
  page.on("request", (request) => observedRequests.push(request.url()));

  if (args.phase === "before-restart") {
    result = await runBeforeRestart(page, browser);
  } else {
    result = await runAfterRestart(page, browser);
  }
  requireOnlyLoopbackHttpRequests(observedRequests);
  await context.close();
} finally {
  await browser.close();
}

await writeFile(args["output-path"], `${JSON.stringify(result)}\n`, {
  encoding: "utf8",
  mode: 0o600,
});

async function runBeforeRestart(page, activeBrowser) {
  await page.goto(baseURL.href);

  await page.getByRole("radio", { name: /Ollama/i }).check();
  await page
    .getByRole("textbox", { name: "Endpoint" })
    .fill(args["provider-endpoint"]);
  await page
    .getByRole("textbox", { name: "Generation model" })
    .fill(args["generation-model"]);
  await page.getByRole("button", { name: "Test and Save" }).click();
  await page.getByText("Provider verified").waitFor({ state: "visible" });
  await requireText(
    page.getByLabel("Runtime status"),
    `Ollama - ${args["generation-model"]}`,
  );

  const question = page.getByRole("textbox", { name: "Question" });
  await question.fill("What Danish test do I need for permanent residence?");
  await page.getByRole("button", { name: "Send" }).click();
  await page
    .getByRole("heading", { name: "Current Conversation" })
    .waitFor({ state: "visible" });
  await requireCount(page.locator(".turn"), 1);
  await page.locator(".answer-section-official_fact").first().waitFor();
  const evidenceTrigger = page
    .getByRole("button", { name: /Inspect evidence:/i })
    .first();
  await evidenceTrigger.waitFor({ state: "visible" });

  const exportAction = await page
    .locator('.conversation-actions form[action$="/export.json"]')
    .getAttribute("action");
  const conversationId = exportAction?.match(
    /\/conversations\/([^/]+)\/export\.json$/,
  )?.[1];
  if (!conversationId) throw new Error("Conversation state was not exposed");
  await writeFile(
    args["state-path"],
    `${JSON.stringify({ conversation_id: conversationId })}\n`,
    { encoding: "utf8", mode: 0o600 },
  );

  await evidenceTrigger.click();
  const drawer = page.getByRole("dialog").first();
  await drawer.waitFor({ state: "visible" });
  for (const label of [
    "Claim Support",
    "Evidence Confidence",
    "Fresh Tomato Score",
    "Publisher",
    "Official URL",
    "Corpus",
    "Model",
  ]) {
    await requireText(drawer, label);
  }
  await page.getByRole("button", { name: "Close evidence drawer" }).click();
  await drawer.waitFor({ state: "hidden" });

  await question.fill(
    "Give me legal advice on how to argue that my Danish test should count.",
  );
  await page.getByRole("button", { name: "Send" }).click();
  await requireCount(page.locator(".turn"), 2);
  await page.locator(".answer-section-refusal").last().waitFor({
    state: "visible",
  });

  const status = await browserJson(page, "/status");
  if (status?.configured !== true || !status.configuration) {
    throw new Error("Runtime configuration was not observed");
  }
  if (
    status.configuration.provider_id !== "ollama" ||
    status.configuration.model !== args["generation-model"]
  ) {
    throw new Error("Observed runtime configuration did not match browser setup");
  }

  return {
    journey_status: {
      setup: "passed",
      "supported-answer": "passed",
      refusal: "passed",
      "evidence-inspection": "passed",
    },
    runtime_configuration: status.configuration,
    browser_identity: browserIdentity(activeBrowser),
  };
}

async function runAfterRestart(page, activeBrowser) {
  const privateState = JSON.parse(
    await readFile(args["state-path"], { encoding: "utf8" }),
  );
  if (
    typeof privateState.conversation_id !== "string" ||
    !privateState.conversation_id
  ) {
    throw new Error("Private browser phase state is invalid");
  }
  const conversationPath = `/conversations/${encodeURIComponent(privateState.conversation_id)}`;
  const historyResponse = await page.goto(new URL(conversationPath, baseURL).href);
  if (!historyResponse?.ok()) throw new Error("Persisted history did not reopen");
  await page
    .getByRole("heading", { name: "Current Conversation" })
    .waitFor({ state: "visible" });
  await requireCount(page.locator(".turn"), 2);
  await page.locator(".answer-section-refusal").last().waitFor();
  await page
    .getByRole("button", { name: /Inspect evidence:/i })
    .first()
    .waitFor();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export record" }).click();
  const download = await downloadPromise;
  const downloadPath = await download.path();
  if (!downloadPath || !download.suggestedFilename().endsWith(".json")) {
    throw new Error("Browser export did not produce a JSON download");
  }

  await page.getByRole("button", { name: "Delete record" }).click();
  await page.waitForURL(baseURL.href);
  const missingResponse = await page.goto(new URL(conversationPath, baseURL).href);
  if (missingResponse?.status() !== 404) {
    throw new Error("Deleted conversation remained browser-accessible");
  }

  await page.goto(baseURL.href);
  await page
    .getByRole("button", { name: "Check for knowledge updates" })
    .click();
  await page
    .getByRole("heading", { name: "Knowledge update available" })
    .waitFor({ state: "visible" });
  await page.getByText(args["target-release-id"], { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Install reviewed release" })
    .click();
  await page
    .getByRole("heading", { name: "Knowledge update installed" })
    .waitFor({ state: "visible", timeout: 600_000 });

  const status = await browserJson(page, "/status");
  if (
    !status?.corpus ||
    status.corpus.knowledge_release_id !== args["target-release-id"]
  ) {
    throw new Error("Installed corpus identity was not observed in the browser");
  }

  return {
    journey_status: {
      "history-persistence": "passed",
      "deletion-export": "passed",
      "update-installation": "passed",
    },
    corpus_identity: status.corpus,
    browser_identity: browserIdentity(activeBrowser),
  };
}

async function browserJson(page, path) {
  return page.evaluate(async (requestPath) => {
    const response = await fetch(requestPath, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Browser JSON request failed");
    return response.json();
  }, path);
}

function browserIdentity(activeBrowser) {
  return {
    name: activeBrowser.browserType().name(),
    version: activeBrowser.version(),
  };
}

function requireOnlyLoopbackHttpRequests(urls) {
  for (const value of urls) {
    const requestURL = new URL(value);
    if (!new Set(["http:", "https:"]).has(requestURL.protocol)) continue;
    if (!new Set(["127.0.0.1", "localhost", "[::1]"]).has(requestURL.hostname)) {
      throw new Error("Browser emitted a non-loopback HTTP request");
    }
  }
}

async function requireText(locator, text) {
  await locator.waitFor({ state: "visible" });
  const content = await locator.textContent();
  if (!content?.includes(text)) throw new Error("Expected browser text was absent");
}

async function requireCount(locator, expected) {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    if ((await locator.count()) === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Expected browser element count was not observed");
}

function parseArguments(values) {
  const parsed = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("Invalid browser monitor arguments");
    }
    parsed[key.slice(2)] = value;
  }
  return parsed;
}
