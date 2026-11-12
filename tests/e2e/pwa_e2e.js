// Drives the real PWA in Chromium against a real relay, with a fake laptop host. Usage: node pwa_e2e.js <port> <token>
const { chromium } = require("playwright");
const [port, TOKEN] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const WEBP_1PX = "UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 6000) {
  const t0 = Date.now();
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch { /* retry */ }
    if (Date.now() - t0 > ms) throw new Error("timeout waiting for: " + what);
    await sleep(50);
  }
}

(async () => {
  const errors = [], hostGot = [];
  const browser = await chromium.launch({ args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/4401|WebSocket connection/.test(m.text())) errors.push("console: " + m.text()); });
  await page.goto(base + "/");

  // 1. pairing screen + wrong token
  await until(() => page.locator("#pair").isVisible(), "pair screen");
  await page.fill("#token", "x".repeat(32));
  await page.click("#pairForm button");
  await until(async () => (await page.textContent("#pairError")).includes("Wrong token"), "wrong token message");

  // 2. correct token
  await page.fill("#token", TOKEN);
  await page.click("#pairForm button");
  await until(() => page.locator("#main").isVisible(), "main view");
  await until(async () => (await page.textContent("#conn")).includes("offline"), "offline label");

  // 3. fake host comes online
  const host = new WebSocket(`ws://127.0.0.1:${port}/ws/host`);
  await new Promise((res, rej) => { host.onopen = res; host.onerror = () => rej(new Error("host ws error")); });
  host.send(JSON.stringify({ type: "auth", token: TOKEN }));
  host.onmessage = (e) => hostGot.push(JSON.parse(e.data));
  await until(async () => (await page.textContent("#conn")).includes("online") && !(await page.textContent("#conn")).includes("offline"), "online label");
  const send = (o) => host.send(JSON.stringify(o));

  // 4. live thought stream, budget, usage, screenshot
  send({ type: "heard", text: "help me here" });
  send({ type: "event", event: "start", persona: "screencoach", query: "help me here" });
  send({ type: "event", event: "tool", name: "capture_foreground_window" });
  send({ type: "event", event: "text", text: "Click " });
  send({ type: "event", event: "text", text: "the Media tab" });
  send({ type: "event", event: "budget", used: 12, cap: 400, warn: false, exhausted: false });
  send({ type: "event", event: "usage", five_hour: 0.31 });
  send({ type: "screenshot", mime: "image/webp", b64: WEBP_1PX });
  await until(async () => (await page.textContent("#stream")).includes("Click the Media tab"), "streamed text");
  const stream = await page.textContent("#stream");
  if (!stream.includes("help me here") || !stream.includes("Using capture foreground window")) throw new Error("stream missing heard/tool: " + stream);
  await until(async () => (await page.textContent("#budgetText")) === "AI 12/400", "budget text");
  await until(async () => (await page.textContent("#usageText")) === "5h 31%", "usage text");
  await until(async () => (await page.getAttribute("#shot", "src") || "").startsWith("data:image/webp;base64,"), "screenshot");
  send({ type: "reply", text: "Click the Media tab", source: "llm", persona: "screencoach", budget: "12/400" });
  await sleep(200);
  const bubbles = await page.locator("#stream .msg.ai").count();
  if (bubbles !== 1) throw new Error("reply should finalize the streamed bubble, got " + bubbles);

  // 5. approval modal -> approve -> host receives it
  send({ type: "approval_request", id: 3, action: "write_markdown", summary: "Write notes/a.md" });
  await until(() => page.locator("#approval").evaluate((d) => d.open), "approval dialog open");
  if (!(await page.textContent("#apSummary")).includes("Write notes/a.md")) throw new Error("approval summary wrong");
  await page.click("#apApprove");
  await until(() => hostGot.find((m) => m.type === "approval_response" && m.id === 3 && m.approved === true), "host got approval");
  await until(() => page.locator("#approval").evaluate((d) => !d.open), "dialog closed");

  // 6. reject path
  send({ type: "approval_request", id: 4, action: "soft_delete_file", summary: "Move a.md to trash" });
  await until(() => page.locator("#approval").evaluate((d) => d.open), "second dialog");
  await page.click("#apReject");
  await until(() => hostGot.find((m) => m.type === "approval_response" && m.id === 4 && m.approved === false), "host got rejection");

  // 7. typed command (EN) and Urdu language toggle
  await page.fill("#say", "mute");
  await page.press("#say", "Enter");
  await until(() => hostGot.find((m) => m.type === "command" && m.text === "mute" && m.lang === "en"), "EN command");
  await page.click("#langUr");
  await page.fill("#say", "آواز کم کرو");
  await page.click("#sayForm button");
  await until(() => hostGot.find((m) => m.type === "command" && m.text === "آواز کم کرو" && m.lang === "ur"), "UR command");
  await page.click("#chips button[data-say='Battery']");
  await until(() => hostGot.find((m) => m.type === "command" && m.text === "Battery"), "chip command");

  // 8. XSS: relayed markup must stay inert text
  send({ type: "reply", text: "<img src=x onerror=window.__xss=1><b>bold</b>", source: "llm", persona: "x" });
  await until(async () => (await page.textContent("#stream")).includes("<img src=x onerror"), "literal markup shown");
  if (await page.evaluate(() => window.__xss)) throw new Error("XSS executed");
  if (await page.locator("#stream img, #stream b").count()) throw new Error("markup was parsed into DOM");
  send({ type: "screenshot", mime: "image/webp", b64: '" onerror="window.__xss=1' });  // malformed base64 must be ignored
  await sleep(200);
  if (await page.evaluate(() => window.__xss)) throw new Error("XSS via screenshot");

  // 9. layout: no horizontal scroll at phone width, dock visible
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  if (overflow) throw new Error("horizontal overflow at 390px");
  if (!(await page.locator("#dock").isVisible())) throw new Error("dock hidden");

  // 10. host goes away -> offline label; reload keeps pairing (token remembered) and restores history
  host.close();
  await until(async () => (await page.textContent("#conn")).includes("offline"), "offline after host close");
  await page.reload();
  await until(() => page.locator("#main").isVisible(), "main after reload");
  await until(async () => (await page.textContent("#stream")).includes("Click the Media tab"), "history restored via snapshot");

  await page.screenshot({ path: process.env.SHOT || "/tmp/pwa_e2e.png" });
  await browser.close();
  if (errors.length) throw new Error("browser errors: " + errors.join(" | "));
  console.log("PWA E2E OK");
})().catch((e) => { console.error("PWA E2E FAIL:", e.message); process.exit(1); });
