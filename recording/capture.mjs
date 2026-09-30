// Capture the README demo from the running isolated app: type a question,
// send it to the real local model, read the cited answer, open its evidence.
// Frames come from the CDP screencast at deviceScaleFactor 2; the cursor is a
// drawn overlay that follows the real dispatched mouse input.
//
// Usage: node recording/capture.mjs <app-url> <out-dir> <question>
// Writes <out-dir>/frames/*.png, frames.json, events.json, rects.json.
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "@playwright/test";

const [url, outDir, question] = process.argv.slice(2);
if (!url || !outDir || !question) throw new Error("usage: capture.mjs <url> <out-dir> <question>");
const VIEWPORT = { width: 1920, height: 1200 };
const SCALE = 2;
const ANSWER_TIMEOUT_MS = 240_000;

const CURSOR_SCRIPT = `
(() => {
  const install = () => {
    if (document.getElementById("demo-cursor")) return;
    const cursor = document.createElement("div");
    cursor.id = "demo-cursor";
    cursor.setAttribute("popover", "manual");
    cursor.setAttribute("aria-hidden", "true");
    cursor.innerHTML = '<svg width="26" height="30" viewBox="0 0 26 30"><path d="M2 2 L2 24 L8 18.5 L12.5 28 L16.5 26.2 L12 17 L20 17 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg><span></span>';
    cursor.style.cssText = "position:fixed;inset:auto;left:0;top:0;margin:0;padding:0;border:0;background:transparent;overflow:visible;pointer-events:none;z-index:2147483647;opacity:0;transform:translate(-100px,-100px);";
    const ring = cursor.querySelector("span");
    ring.style.cssText = "position:absolute;left:-14px;top:-14px;width:28px;height:28px;border-radius:50%;background:rgba(21,94,78,.35);transform:scale(0);transition:transform .18s ease-out,opacity .3s ease-out;opacity:0;";
    document.documentElement.appendChild(cursor);
    cursor.showPopover();
    addEventListener("mousemove", e => {
      cursor.style.opacity = "1";
      cursor.style.transform = "translate(" + (e.clientX - 2) + "px," + (e.clientY - 2) + "px)";
    }, true);
    addEventListener("mousedown", () => { ring.style.transform = "scale(1)"; ring.style.opacity = "1"; }, true);
    addEventListener("mouseup", () => { ring.style.transform = "scale(1.4)"; ring.style.opacity = "0"; setTimeout(() => { ring.style.transform = "scale(0)"; }, 300); }, true);
  };
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", install); else install();
})();`;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const now = () => Date.now() / 1000;
const ease = u => u * u * (3 - 2 * u);

// The screencast ignores emulated scale, so force the device scale factor and
// size the real window instead of emulating a viewport.
const browser = await chromium.launch({
  args: [`--force-device-scale-factor=${SCALE}`, `--window-size=${VIEWPORT.width},${VIEWPORT.height}`],
});
const context = await browser.newContext({ viewport: null, colorScheme: "light" });
await context.addInitScript(CURSOR_SCRIPT);
const page = await context.newPage();
const pointer = { x: 1300, y: 760 };

// Motion is paced by the clock, not by step count: each input waits for a
// captured frame, so fixed steps would stretch the motion.
async function animate(ms, step) {
  const t0 = Date.now();
  for (;;) {
    const u = Math.min((Date.now() - t0) / ms, 1);
    await step(ease(u));
    if (u >= 1) return;
    await sleep(8);
  }
}

async function glide(x, y, ms = 700) {
  const from = { ...pointer };
  await animate(ms, async u => {
    pointer.x = from.x + (x - from.x) * u;
    pointer.y = from.y + (y - from.y) * u;
    await page.mouse.move(pointer.x, pointer.y);
  });
}

async function center(locator) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("Target is not visible.");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function rect(selector) {
  return page.evaluate(sel => {
    const r = document.querySelector(sel).getBoundingClientRect();
    return [r.x, r.y, r.width, r.height];
  }, selector);
}

// One real wheel event per scroll: full-frame 2x PNG capture only reaches
// ~4 fps while text scrolls, so a clean jump reads better than a stutter.
async function wheelJump(total) {
  await page.mouse.wheel(0, total);
}
await mkdir(join(outDir, "frames"), { recursive: true });
await page.goto(url, { waitUntil: "networkidle" });
await page.mouse.move(pointer.x, pointer.y);
await sleep(500);

const cdp = await context.newCDPSession(page);
const frames = [];
const writes = [];
cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
  const file = `frames/${String(frames.length).padStart(5, "0")}.png`;
  frames.push({ file, t: metadata.timestamp });
  writes.push(writeFile(join(outDir, file), Buffer.from(data, "base64")));
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});
await cdp.send("Page.startScreencast", {
  format: "png", // lossless: JPEG noise changes every pixel and bloats the GIF
  maxWidth: VIEWPORT.width * SCALE,
  maxHeight: VIEWPORT.height * SCALE,
  everyNthFrame: 1,
});

const events = {};
const rects = {};
const mark = name => { events[name] = now(); };

mark("start");
await sleep(1000);

// Below ~1200px tall the home composer is clipped by the conversation column
// (it cannot be scrolled to), so the viewport is sized to show it; never fake it.
const clipped = await page.evaluate(() => document.querySelector(".composer").getBoundingClientRect().bottom > innerHeight);
if (clipped) throw new Error("The composer is outside the viewport; use a taller viewport.");
mark("composer_ready");
const composer = page.locator("#question");
rects.composer = await rect(".composer");
const box = await composer.boundingBox();
await glide(box.x + 60, box.y + 36, 800);
mark("type_start");
await page.mouse.down(); await page.mouse.up();
await sleep(250);
await page.keyboard.type(question, { delay: 85 });
mark("type_end");
await sleep(500);
const send = await center(page.locator(".composer button[type=submit]"));
await glide(send.x, send.y, 600);
await sleep(150);
await page.mouse.down(); await page.mouse.up();
mark("send");

const trigger = page.locator("[data-evidence-target]").first();
await trigger.waitFor({ state: "attached", timeout: ANSWER_TIMEOUT_MS });
mark("answer");
await sleep(900);

// Bring the answer's first line to the top of the conversation column.
const offset = await page.evaluate(() => {
  const stack = document.querySelector(".turn-stack");
  const answer = [...document.querySelectorAll(".turn .answer")].at(-1);
  return answer.getBoundingClientRect().top - stack.getBoundingClientRect().top - 8;
});
const stackBox = await rect(".turn-stack");
await glide(stackBox[0] + stackBox[2] * 0.6, stackBox[1] + stackBox[3] * 0.5, 600);
mark("scroll");
await wheelJump(offset);
rects.answer = await rect(".turn-stack");
mark("reading");
// Park the cursor beside the column so it does not cover the answer.
await glide(stackBox[0] + stackBox[2] + 60, stackBox[1] + stackBox[3] * 0.45, 500);
await sleep(2800);

await trigger.scrollIntoViewIfNeeded();
const target = await center(trigger);
await glide(target.x - 40, target.y, 700);
await sleep(200);
mark("evidence_click");
await page.mouse.down(); await page.mouse.up();
await page.locator("dialog[open]").waitFor();
await page.evaluate(() => { const c = document.getElementById("demo-cursor"); c.hidePopover(); c.showPopover(); });
mark("drawer");
rects.drawer = await rect("dialog[open] .drawer-panel");
await sleep(2200);
await glide(rects.drawer[0] + rects.drawer[2] * 0.55, 560, 600);
await wheelJump(260);
mark("drawer_scrolled");
await sleep(2000);
mark("end");

await cdp.send("Page.stopScreencast");
await Promise.all(writes);
const answerText = await page.evaluate(() => [...document.querySelectorAll(".turn")].at(-1).innerText);
await browser.close();

const t0 = events.start;
const rel = t => Math.round((t - t0) * 1000) / 1000;
await writeFile(join(outDir, "frames.json"), JSON.stringify(frames.map(f => ({ file: f.file, t: rel(f.t) })), null, 2));
await writeFile(join(outDir, "events.json"), JSON.stringify(Object.fromEntries(Object.entries(events).map(([k, v]) => [k, rel(v)])), null, 2));
await writeFile(join(outDir, "rects.json"), JSON.stringify({ viewport: [VIEWPORT.width, VIEWPORT.height], scale: SCALE, ...rects }, null, 2));
await writeFile(join(outDir, "answer.txt"), answerText);
console.log(`captured ${frames.length} frames, wait ${(events.answer - events.send).toFixed(1)}s`);
