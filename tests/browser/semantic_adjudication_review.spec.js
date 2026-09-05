import { expect, test } from "@playwright/test";
import { pathToFileURL } from "node:url";
import path from "node:path";

const REVIEW_PAGE = pathToFileURL(
  path.resolve("review/semantic-adjudication-review.html"),
).href;

function reviewPacket({
  executionHash = "a".repeat(64),
  reviewPayloadHash = "b".repeat(64),
} = {}) {
  return {
    schema_version: "final-answer-human-review-packet-v1",
    classification: "sensitive-local-only",
    commit_policy: "do-not-commit",
    contains_human_decisions: false,
    generated_at_utc: "2026-07-24T12:00:00Z",
    dataset: {
      dataset_id: "di-rag-eval-set-v0.1-candidate",
      version: "0.1.0-candidate",
      sha256: "c".repeat(64),
    },
    case_count: 1,
    cases: [
      {
        case_id: "eval-fixture-review",
        evaluation_surface: "answer-path",
        prompt: "Project-authored synthetic browser fixture prompt.",
        assertions: [
          {
            assertion_id: "eval-fixture-review:required-facts:01",
            expectation_group: "required_facts",
            criterion: "State the project-authored fixture fact.",
          },
        ],
        execution_sha256: executionHash,
        review_payload_sha256: reviewPayloadHash,
        execution: {
          case_id: "eval-fixture-review",
          error_type: "",
          result: {
            question: "Project-authored synthetic browser fixture prompt.",
            normalized_question: "project-authored synthetic browser fixture prompt.",
            answer: {
              summary: "Fixture answer.",
              response_kind: "answer",
              sections: [
                {
                  kind: "official_fact",
                  text: "Project-authored fixture fact.",
                  citation_ids: ["fixture-source"],
                },
              ],
              trust: {
                evidence_confidence: "High",
                fresh_tomato_score: "High",
              },
            },
            model_identity: {
              provider_id: "ollama",
              model: "gemma4:12b",
            },
            corpus_identity: "kr-2026-07-06.1",
          },
          evidence: [
            {
              citation_id: "fixture-source",
              content: "Project-authored fixture fact.",
              official_url: "https://nyidanmark.dk/fixture",
              review_state: "approved-current",
            },
          ],
        },
        blank_adjudication_template: {
          schema_version: "final-answer-case-adjudication-v1",
          case_id: "eval-fixture-review",
          evaluation_surface: "answer-path",
          evidence_binding: {
            kind: "answer-review-payload",
            sha256: reviewPayloadHash,
            execution_sha256: executionHash,
          },
          assessment_method: "independent-human-review",
          assertion_results: {
            "eval-fixture-review:required-facts:01": null,
          },
          claim_support: {
            "section-1": {
              "fixture-source": null,
            },
          },
        },
      },
    ],
  };
}

async function loadPacket(page, packet) {
  await page.locator("#packet-file").setInputFiles({
    name: "synthetic-review-packet.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(packet)),
  });
  await expect(page.locator("#review-app")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto(REVIEW_PAGE);
});

test("accepted export requires complete review and explicit independent-human attestation", async ({
  page,
}) => {
  const networkRequests = [];
  page.on("request", (request) => {
    if (/^https?:/.test(request.url())) networkRequests.push(request.url());
  });
  const packet = reviewPacket();
  await loadPacket(page, packet);

  const acceptedExport = page.getByRole("button", {
    name: "Export accepted adjudications",
  });
  const attestation = page.getByRole("checkbox", {
    name: /I attest that I am an independent human reviewer/i,
  });
  await expect(acceptedExport).toBeDisabled();
  await attestation.check();
  await expect(acceptedExport).toBeDisabled();

  await page
    .locator("#assertions-body button[data-value='passed']")
    .click();
  await page
    .locator("#support-body button[data-value='true']")
    .click();
  await expect(page.locator("#progress-count")).toHaveText("2 / 2");
  await expect(acceptedExport).toBeEnabled();

  const downloadPromise = page.waitForEvent("download");
  await acceptedExport.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(
    "danish-rag-final-answer-adjudications-v1.json",
  );
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const exported = JSON.parse(Buffer.concat(chunks).toString("utf-8"));

  expect(exported.schema_version).toBe("final-answer-adjudications-v1");
  expect(exported.dataset).toEqual(packet.dataset);
  expect(exported.independent_human_attestation).toMatchObject({
    schema_version: "final-answer-independent-human-attestation-v1",
    attested: true,
    packet_schema_version: packet.schema_version,
    dataset: packet.dataset,
    case_bindings: [
      {
        case_id: packet.cases[0].case_id,
        execution_sha256: packet.cases[0].execution_sha256,
        review_payload_sha256: packet.cases[0].review_payload_sha256,
      },
    ],
  });
  expect(exported.cases).toHaveLength(1);
  expect(exported.cases[0]).toEqual({
    ...packet.cases[0].blank_adjudication_template,
    assertion_results: {
      "eval-fixture-review:required-facts:01": "passed",
    },
    claim_support: {
      "section-1": {
        "fixture-source": true,
      },
    },
    review_notes: "",
  });
  expect(networkRequests).toEqual([]);
});

test("a different exact packet fingerprint cannot inherit saved decisions", async ({
  page,
}) => {
  await loadPacket(page, reviewPacket());
  await page
    .locator("#assertions-body button[data-value='passed']")
    .click();
  await page
    .locator("#support-body button[data-value='true']")
    .click();
  await expect(page.locator("#progress-count")).toHaveText("2 / 2");

  await page.getByRole("button", { name: "Load another packet" }).click();
  await loadPacket(
    page,
    reviewPacket({
      executionHash: "a".repeat(64),
      reviewPayloadHash: "e".repeat(64),
    }),
  );

  await expect(page.locator("#progress-count")).toHaveText("0 / 2");
  await expect(
    page.getByRole("checkbox", {
      name: /I attest that I am an independent human reviewer/i,
    }),
  ).not.toBeChecked();
  await expect(
    page.getByRole("button", { name: "Export accepted adjudications" }),
  ).toBeDisabled();
});

function contextualPacket() {
  const packet = reviewPacket();
  const item = packet.cases[0];
  const identity = {
    source_id: "reviewed-page",
    source_document_id: "reviewed-document",
    source_content_sha256: "1".repeat(64),
    normalized_document_sha256: "2".repeat(64),
    normalized_extraction_sha256: "3".repeat(64),
    official_url: "https://nyidanmark.dk/fixture",
    final_url: "https://nyidanmark.dk/fixture",
    language: "en-GB",
    corpus_identity: "kr-fixture",
    knowledge_release_id: "kr-fixture",
  };
  item.execution.evidence[0] = { ...item.execution.evidence[0], ...identity };
  item.execution.evidence.push({
    ...identity, citation_id: "fixture-context", content: "The cited heading supplies the examination context.",
  });
  item.execution.evidence.push({
    ...identity, citation_id: "uncited-context", content: "Uncited context must never support the decision.",
  });
  item.execution.result.answer.sections[0].citation_ids.push("fixture-context");
  item.blank_adjudication_template.claim_support["section-1"]["fixture-context"] = null;
  return packet;
}

test("joint source context shows only explicit citations and leaves every judgment blank", async ({ page }) => {
  await loadPacket(page, contextualPacket());
  const first = page.locator("#support-body .relationship-card").first();
  await expect(first).toContainText("Does this citation materially contribute");
  await expect(first.locator(".joint-source-context")).toContainText("The cited heading supplies the examination context.");
  await expect(first.locator(".joint-source-context")).not.toContainText("Uncited context must never support the decision.");
  await expect(first).toContainText("An irrelevant citation still fails.");
  await expect(page.locator("#support-body button[aria-pressed='true']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export accepted adjudications" })).toBeDisabled();
});

test("missing or mismatched provenance cannot join source context", async ({ page }) => {
  for (const mutate of [
    (evidence) => { delete evidence.normalized_document_sha256; },
    (evidence) => { evidence.normalized_extraction_sha256 = "4".repeat(64); },
    (evidence) => { evidence.source_document_id = "different-document"; },
  ]) {
    const packet = contextualPacket();
    mutate(packet.cases[0].execution.evidence[1]);
    await loadPacket(page, packet);
    await expect(page.locator("#support-body .joint-source-context")).toHaveCount(0);
    await page.getByRole("button", { name: "Load another packet" }).click();
  }
});

test("same claim and focused citation cannot share decisions across changed cited context", async ({ page }) => {
  const packet = contextualPacket();
  const second = structuredClone(packet.cases[0]);
  second.case_id = "eval-fixture-context-changed";
  second.execution.case_id = second.case_id;
  second.blank_adjudication_template.case_id = second.case_id;
  second.execution_sha256 = "d".repeat(64);
  second.review_payload_sha256 = "e".repeat(64);
  second.blank_adjudication_template.evidence_binding.execution_sha256 = second.execution_sha256;
  second.blank_adjudication_template.evidence_binding.sha256 = second.review_payload_sha256;
  second.execution.evidence[1].content = "A different heading changes the scope of the same tail passage.";
  packet.cases.push(second);
  packet.case_count = 2;
  await loadPacket(page, packet);
  await page.locator("#support-body .relationship-card").first().locator("button[data-value='true']").click();
  await page.locator("#case-nav button").nth(1).click();
  await expect(page.locator("#support-body button[aria-pressed='true']")).toHaveCount(0);
  await expect(page.locator("#support-body .relationship-card").first()).not.toContainText("Exact duplicate");
});

test("older context-free saved decisions remain stored but are not inherited", async ({ page }) => {
  const packet = reviewPacket();
  const item = packet.cases[0];
  const oldKey = `danish-rag-product-owner-review:${packet.dataset.sha256}:${item.case_id}:${item.execution_sha256}:${item.review_payload_sha256}`;
  const saved = JSON.stringify({ cases: { [item.case_id]: {
    assertion_results: { "eval-fixture-review:required-facts:01": "passed" },
    claim_support: { "section-1": { "fixture-source": true } }, notes: "Existing private notes",
  } } });
  await page.evaluate(({ oldKey, saved }) => window.localStorage.setItem(oldKey, saved), { oldKey, saved });
  await loadPacket(page, packet);
  await expect(page.locator("#progress-count")).toHaveText("0 / 2");
  expect(await page.evaluate((key) => window.localStorage.getItem(key), oldKey)).toBe(saved);
});
