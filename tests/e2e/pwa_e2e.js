// Drives the real chat UI in Chromium against a real relay with a fake laptop host.
// Usage: node pwa_e2e.js <relayPort> <token> <pwaDir> [shotsDir]
const { chromium } = require("playwright");
const http = require("http"), fs = require("fs"), path = require("path");
const [port, TOKEN, PWA_DIR, SHOTS = "/tmp"] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const WEBP_1PX = "UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, what, ms = 6000) {
  const t0 = Date.now();
  for (;;) { try { const v = await fn(); if (v) return v; } catch { /* retry */ } if (Date.now() - t0 > ms) throw new Error("timeout waiting for: " + what); await sleep(40); }
}
function assert(c, msg) { if (!c) throw new Error("assertion failed: " + msg); }

async function connectHost(token = TOKEN) {
  const got = [];
  const ws = new WebSocket(`ws://127.0.0.1:${port}/ws/host`);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("host ws error")); });
  ws.onmessage = (e) => got.push(JSON.parse(e.data));
  ws.send(JSON.stringify({ type: "auth", token }));
  return { ws, got, send: (o) => ws.send(JSON.stringify(o)) };
}
const text = (page, sel) => page.textContent(sel);

(async () => {
  const errors = [];
  const browser = await chromium.launch({ args: ["--no-sandbox"] });
  const watch = (page, tag) => {
    page.on("pageerror", (e) => errors.push(`${tag} pageerror: ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error" && !/4401|WebSocket connection|favicon/.test(m.text())) errors.push(`${tag} console: ${m.text()}`); });
  };

  /* ============================ A. mobile browser (PWA served by relay, strict CSP) ============================ */
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: "dark" });
  await ctx.addInitScript(() => {   // stub Web Speech API: dictate "mute" when started
    window.SpeechRecognition = window.webkitSpeechRecognition = class { start() { setTimeout(() => { this.onresult && this.onresult({ results: [Object.assign([{ transcript: "mute" }], { isFinal: true })] }); this.onend && this.onend(); }, 80); } stop() {} };
  });
  const page = await ctx.newPage(); watch(page, "mobile");
  await page.goto(base + "/");

  // onboarding (web build never asks for a server address)
  await until(() => page.locator("#onboard").isVisible(), "onboarding");
  assert(!(await page.locator("#obServerRow").isVisible()), "web onboarding must not show server field");
  await page.fill("#obToken", "x".repeat(32)); await page.click("#obForm button[type=submit]");
  await until(async () => (await text(page, "#obError")).includes("not accepted"), "wrong token message");
  await page.fill("#obToken", "short"); await page.click("#obForm button[type=submit]");
  await until(async () => (await text(page, "#obError")).includes("too short"), "short token message");
  await page.fill("#obToken", TOKEN); await page.click("#obForm button[type=submit]");
  await until(() => page.locator("#app").isVisible(), "app visible");
  await until(async () => (await text(page, "#banner")).includes("laptop is offline"), "offline banner");
  assert(await page.locator("#empty").isVisible(), "empty state shown");

  const host = await connectHost(); const send = host.send;
  await until(async () => !(await page.locator("#banner").isVisible()), "banner gone when laptop online");
  await page.screenshot({ path: path.join(SHOTS, "01-empty-dark.png") });

  // local command via suggestion; host echoes "heard" -> must not duplicate the user bubble
  await page.click("#suggestions button[data-say='Battery']");
  await until(() => host.got.find((m) => m.type === "command" && m.text === "Battery" && m.lang === "en"), "host got Battery");
  send({ type: "heard", text: "Battery" });
  send({ type: "reply", text: "CPU 8%, RAM 4%, Battery 90%", source: "local", persona: "sysop" });
  await until(async () => (await text(page, "#thread")).includes("CPU 8%"), "local reply");
  assert((await page.locator(".row.user").count()) === 1, "no duplicate user bubble from heard echo");
  assert(!(await page.locator("#empty").isVisible()), "empty state hidden after first message");

  // LLM flow: streaming, tool steps, markdown, screenshot, meters, persona chip
  await page.fill("#input", "help me here"); await page.click("#btnSend");
  await until(() => host.got.find((m) => m.type === "command" && m.text === "help me here"), "host got LLM command");
  send({ type: "heard", text: "help me here" });
  send({ type: "event", event: "start", persona: "screencoach", query: "help me here" });
  send({ type: "event", event: "tool", name: "capture_foreground_window" });
  send({ type: "screenshot", mime: "image/webp", b64: WEBP_1PX });
  await until(async () => (await page.locator(".steps").last().textContent()).includes("Looking at your screen"), "tool step label");
  assert(await page.locator(".typing").count() === 1, "typing indicator while waiting for text");
  send({ type: "event", event: "text", text: "Click **the Media tab**, then:\n\n- open `Import`\n- pick a clip\n\n```\nCtrl+I\n```" });
  send({ type: "event", event: "budget", used: 12, cap: 400, warn: false, exhausted: false });
  send({ type: "event", event: "usage", five_hour: 0.31 });
  await until(() => page.locator(".content strong").count(), "bold rendered");
  assert((await page.locator(".content li").count()) === 2, "two list items");
  assert((await text(page, ".content pre")).includes("Ctrl+I"), "code block");
  assert((await page.locator(".content code").count()) >= 2, "inline + block code");
  assert((await text(page, "#persona")) === "Screen coach", "persona chip");
  assert((await page.locator(".shot-thumb img").count()) === 1, "screenshot thumbnail inside the reply");
  send({ type: "reply", text: "Click **the Media tab**, then:\n\n- open `Import`\n- pick a clip\n\n```\nCtrl+I\n```", source: "llm", persona: "screencoach" });
  await until(() => page.locator(".msg-actions:not([hidden])").count(), "copy action visible when done");
  assert((await page.locator(".typing").count()) === 0, "typing gone");
  await page.click("#btnMenu");
  await until(async () => (await text(page, "#budgetText")) === "12 / 400", "budget text");
  assert((await text(page, "#usageText")) === "31%", "usage text");
  assert((await text(page, "#sbStatus")) === "Laptop online", "sidebar status");
  await page.screenshot({ path: path.join(SHOTS, "02-sidebar-dark.png") });
  await page.click("#scrim", { position: { x: 380, y: 400 } });
  await page.screenshot({ path: path.join(SHOTS, "03-chat-dark.png") });

  // double-send guard
  await page.fill("#input", "one"); await page.click("#btnSend");
  await until(() => host.got.find((m) => m.type === "command" && m.text === "one"), "first send");
  await page.fill("#input", "two"); await page.click("#btnSend");
  await until(async () => (await text(page, "#thread")).includes("still working on your last request"), "busy guard note");
  assert(!host.got.find((m) => m.type === "command" && m.text === "two"), "second command must not reach the host");
  send({ type: "reply", text: "done", source: "local", persona: "sysop" });
  await until(async () => { await page.fill("#input", "three"); await page.click("#btnSend"); return host.got.find((m) => m.text === "three"); }, "send works again after reply");
  send({ type: "reply", text: "ok", source: "local" });

  // approval bottom sheet
  send({ type: "approval_request", id: 3, action: "write_markdown", summary: "Write notes/a.md" });
  await until(() => page.locator("#approval").evaluate((d) => d.open), "approval open");
  assert((await text(page, "#apTitle")).includes("write a note"), "friendly action title");
  await page.screenshot({ path: path.join(SHOTS, "04-approval-dark.png") });
  await page.click("#apApprove");
  await until(() => host.got.find((m) => m.type === "approval_response" && m.id === 3 && m.approved === true), "approved reaches host");
  send({ type: "approval_request", id: 4, action: "soft_delete_file", summary: "Move a.md to trash" });
  await until(() => page.locator("#approval").evaluate((d) => d.open), "second approval");
  await page.click("#apReject");
  await until(() => host.got.find((m) => m.type === "approval_response" && m.id === 4 && m.approved === false), "rejection reaches host");

  // dictation (stubbed Web Speech): spoken "mute" is sent automatically
  await page.click("#btnMic");
  await until(() => host.got.find((m) => m.type === "command" && m.text === "mute"), "dictated command sent");
  send({ type: "reply", text: "Done.", source: "local" });

  // Urdu: language toggle, RTL-aware bubble
  await page.click("#btnLang"); assert((await text(page, "#btnLang")) === "اردو", "language pill");
  await page.fill("#input", "آواز کم کرو"); await page.click("#btnSend");
  await until(() => host.got.find((m) => m.type === "command" && m.text === "آواز کم کرو" && m.lang === "ur"), "urdu command + lang");
  send({ type: "reply", text: "آواز تبدیل ہو گئی۔", source: "local" });
  await until(async () => (await text(page, "#thread")).includes("آواز تبدیل ہو گئی"), "urdu reply");
  await page.screenshot({ path: path.join(SHOTS, "05-urdu-dark.png") });

  // XSS: markup and javascript: links stay inert text
  send({ type: "reply", text: "<img src=x onerror=window.__xss=1> [click](javascript:window.__xss=2) <b>x</b>", source: "llm", persona: "screencoach" });
  await until(async () => (await text(page, "#thread")).includes("<img src=x onerror"), "markup shown literally");
  assert(!(await page.evaluate(() => window.__xss)), "XSS executed");
  assert((await page.locator("#thread .content img, #thread .content a, #thread .content b, #thread .bubble img").count()) === 0, "markup was parsed into DOM");
  send({ type: "screenshot", mime: "image/webp", b64: '" onerror="window.__xss=1' });
  await sleep(150); assert(!(await page.evaluate(() => window.__xss)), "XSS via screenshot");

  // chat history: new chat -> old chat is in the sidebar and restorable; persists across reload
  await page.click("#btnNew");
  await until(() => page.locator("#empty").isVisible(), "new chat empty state");
  await page.click("#btnMenu");
  const item = page.locator("#chatList .chat-item").first();
  await until(() => item.isVisible(), "history item");
  assert((await item.textContent()).includes("Battery"), "chat titled from first message");
  await item.click();
  await until(async () => (await text(page, "#thread")).includes("Click the Media tab"), "chat restored");
  await page.reload();
  await until(() => page.locator("#app").isVisible(), "app after reload (token remembered)");
  await until(async () => (await text(page, "#thread")).includes("Click the Media tab"), "history persisted across reload");

  // theme + layout
  await page.click("#btnMenu"); await page.click("#btnSettings");
  await page.selectOption("#stTheme", "light");
  assert((await page.evaluate(() => document.documentElement.dataset.theme)) === "light", "light theme");
  await page.click("#stClose");
  await page.screenshot({ path: path.join(SHOTS, "06-chat-light.png") });
  await page.click("#btnMenu"); await page.click("#btnSettings"); await page.selectOption("#stTheme", "dark"); await page.click("#stClose");
  assert(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), "no horizontal overflow at 390px");
  assert(await page.locator("#composer").isVisible(), "composer visible");

  // laptop drops -> banner; send blocked with a note
  host.ws.close();
  await until(async () => (await text(page, "#banner")).includes("laptop is offline"), "offline banner after host closes");
  await page.fill("#input", "hello?"); await page.click("#btnSend");
  await until(async () => (await text(page, "#thread")).includes("Your laptop is offline."), "send blocked while offline");

  // clear chats (two-tap confirm)
  await page.click("#btnMenu"); await page.click("#btnSettings");
  await page.click("#stClear"); await page.click("#stClear");
  await until(() => page.locator("#empty").isVisible(), "chats cleared");

  /* ============================ B. desktop layout: persistent sidebar, Enter sends ============================ */
  const dctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "light" });
  const dpage = await dctx.newPage(); watch(dpage, "desktop");
  await dpage.goto(base + "/");
  await dpage.fill("#obToken", TOKEN); await dpage.click("#obForm button[type=submit]");
  await until(() => dpage.locator("#app").isVisible(), "desktop app");
  assert(await dpage.locator("#sidebar").isVisible() && !(await dpage.locator("#btnMenu").isVisible()), "desktop: sidebar always visible, no hamburger");
  const host2 = await connectHost();
  await until(async () => !(await dpage.locator("#banner").isVisible()), "desktop online");
  await dpage.fill("#input", "lock screen"); await dpage.press("#input", "Enter");
  await until(() => host2.got.find((m) => m.type === "command" && m.text === "lock screen"), "Enter sends on desktop");
  host2.send({ type: "reply", text: "Locking.", source: "local" });
  await until(async () => (await text(dpage, "#thread")).includes("Locking."), "desktop reply");
  await dpage.screenshot({ path: path.join(SHOTS, "07-desktop-light.png") });
  host2.ws.close(); await dctx.close();

  /* ============================ C. simulated Android app: Capacitor bridge, cross-origin server ============================ */
  const appServer = http.createServer((req, res) => {   // serves the web bundle from a DIFFERENT origin, like https://localhost in the APK
    const f = path.join(PWA_DIR, req.url === "/" ? "index.html" : decodeURIComponent(req.url.split("?")[0]));
    if (!f.startsWith(PWA_DIR) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); return res.end(); }
    const ext = path.extname(f); res.writeHead(200, { "Content-Type": { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".webmanifest": "application/json" }[ext] || "application/octet-stream" });
    fs.createReadStream(f).pipe(res);
  });
  await new Promise((r) => appServer.listen(0, "127.0.0.1", r));
  const appPort = appServer.address().port;
  const actx = await browser.newContext({ viewport: { width: 393, height: 851 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: "light" });
  await actx.addInitScript(() => {
    window.__capNoDev = true;
    window.__cap = { handlers: {}, calls: [], exited: false };
    const plugins = {
      App: { addListener: (n, cb) => { window.__cap.handlers[n] = cb; return Promise.resolve({ remove() {} }); }, exitApp: () => { window.__cap.exited = true; } },
      SpeechRecognition: {
        available: async () => ({ available: true }), checkPermissions: async () => ({ speechRecognition: "granted" }), requestPermissions: async () => ({ speechRecognition: "granted" }),
        addListener: async (n, cb) => { window.__cap.handlers[n] = cb; return { remove() {} }; },
        start: async (o) => { window.__cap.calls.push(o); setTimeout(() => { window.__cap.handlers.partialResults({ matches: ["batt"] }); window.__cap.handlers.partialResults({ matches: ["battery"] }); setTimeout(() => window.__cap.handlers.listeningState({ status: "stopped" }), 60); }, 60); },
        stop: async () => {},
      },
    };
    window.Capacitor = { isNativePlatform: () => true, registerPlugin: (n) => plugins[n] };
  });
  const apage = await actx.newPage(); watch(apage, "android");
  await apage.goto(`http://127.0.0.1:${appPort}/`);
  await until(() => apage.locator("#onboard").isVisible(), "android onboarding");
  assert(await apage.locator("#obServerRow").isVisible(), "native onboarding asks for the server address");
  await apage.fill("#obServer", "http://evil.example.com"); await apage.fill("#obToken", TOKEN); await apage.click("#obForm button[type=submit]");
  await until(async () => (await text(apage, "#obError")).includes("https://"), "plain-http public host rejected");
  await apage.screenshot({ path: path.join(SHOTS, "08-android-onboarding.png") });
  await apage.fill("#obServer", `http://127.0.0.1:${port}`); await apage.fill("#obToken", TOKEN); await apage.click("#obForm button[type=submit]");
  await until(async () => (await text(apage, "#obError")).includes("https://"), "native app refuses cleartext http without the dev flag");
  await apage.evaluate(() => localStorage.setItem("nyro_dev_http", "1"));   // developer flag: allow LAN http for this test
  await apage.fill("#obServer", `http://127.0.0.1:${port}`); await apage.fill("#obToken", TOKEN); await apage.click("#obForm button[type=submit]");
  await until(() => apage.locator("#app").isVisible(), "android app visible");
  const host3 = await connectHost();
  await until(async () => (await text(apage, "#sbStatus")) === "Laptop online", "android connected cross-origin");
  await apage.click("#btnMic");                                             // native dictation: partial results then 'stopped' => auto-send
  await until(() => host3.got.find((m) => m.type === "command" && m.text === "battery"), "native dictation sent the final partial result");
  assert((await apage.evaluate(() => window.__cap.calls[0].language)) === "en-US" && (await apage.evaluate(() => window.__cap.calls[0].partialResults)) === true, "plugin called with language + partialResults");
  host3.send({ type: "reply", text: "Battery 90%", source: "local" });
  await until(async () => (await text(apage, "#thread")).includes("Battery 90%"), "android reply");
  await apage.screenshot({ path: path.join(SHOTS, "09-android-chat.png") });
  // hardware back button: closes dialog -> closes sidebar -> new chat -> exits
  await apage.click("#btnMenu"); await apage.click("#btnSettings");
  await apage.evaluate(() => window.__cap.handlers.backButton());
  assert(!(await apage.locator("#settings").evaluate((d) => d.open)), "back closes dialog");
  await apage.evaluate(() => window.__cap.handlers.backButton());           // sidebar was closed by opening settings -> chat non-empty -> new chat
  await until(() => apage.locator("#empty").isVisible(), "back starts a new chat");
  await apage.evaluate(() => window.__cap.handlers.backButton());
  assert(await apage.evaluate(() => window.__cap.exited), "back on empty chat exits the app");
  assert(await apage.evaluate(() => !("serviceWorker" in navigator) || true), "ok");
  const regs = await apage.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length);
  assert(regs === 0, "native build must not register a service worker");
  host3.ws.close(); appServer.close();

  await browser.close();
  if (errors.length) throw new Error("browser errors: " + errors.join(" | "));
  console.log("PWA E2E OK");
})().catch((e) => { console.error("PWA E2E FAIL:", e.message); process.exit(1); });
