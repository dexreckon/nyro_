const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../../pwa/nyro-lib.js");
const M = require("../../pwa/nyro-md.js");

test("normalizeServer: scheme, paths, ports", () => {
  assert.deepEqual(L.normalizeServer("nyro.example.com"), { ok: true, origin: "https://nyro.example.com" });
  assert.deepEqual(L.normalizeServer("  https://nyro.example.com/some/path?x=1#y "), { ok: true, origin: "https://nyro.example.com" });
  assert.deepEqual(L.normalizeServer("wss://nyro.example.com/ws/mobile"), { ok: true, origin: "https://nyro.example.com" });
  assert.deepEqual(L.normalizeServer("https://nyro.example.com:8443"), { ok: true, origin: "https://nyro.example.com:8443" });
});

test("normalizeServer: http only on local/LAN hosts", () => {
  for (const ok of ["http://localhost:8000", "http://127.0.0.1:8000", "http://192.168.1.20:8000", "http://10.0.0.5", "http://172.20.1.1"]) {
    assert.equal(L.normalizeServer(ok).ok, true, ok);
  }
  for (const bad of ["http://evil.example.com", "http://8.8.8.8", "http://172.32.0.1", "http://192.168.1.1.evil.com"]) {
    assert.equal(L.normalizeServer(bad).ok, false, bad);
  }
});

test("normalizeServer: allowLocalHttp=false (Android app) is https-only", () => {
  assert.equal(L.normalizeServer("http://192.168.1.5:8000", { allowLocalHttp: false }).ok, false);
  assert.equal(L.normalizeServer("http://localhost:8000", { allowLocalHttp: false }).ok, false);
  assert.equal(L.normalizeServer("nyro.example.com", { allowLocalHttp: false }).ok, true);
  assert.equal(L.normalizeServer("http://192.168.1.5:8000", { allowLocalHttp: true }).ok, true);
});

test("normalizeServer: rejects junk", () => {
  for (const bad of ["", "   ", "ftp://x.com", "javascript:alert(1)", "https://user:pw@x.com", "http://", "https://"]) {
    assert.equal(L.normalizeServer(bad).ok, false, JSON.stringify(bad));
  }
});

test("wsUrl", () => {
  assert.equal(L.wsUrl("https://a.com"), "wss://a.com/ws/mobile");
  assert.equal(L.wsUrl("http://192.168.1.2:8000"), "ws://192.168.1.2:8000/ws/mobile");
  assert.equal(L.wsUrl("https://a.com", "host"), "wss://a.com/ws/host");
});

test("labels and titles", () => {
  assert.equal(L.actionLabel("write_markdown"), "write a note");
  assert.equal(L.actionLabel("some_new_action"), "some new action");
  assert.equal(L.toolLabel("capture_foreground_window"), "Looking at your screen");
  // every tool the laptop can run has a friendly label (new modes: control, code, file manager)
  for (const [tool, label] of Object.entries({ search_files: "Searching your files", write_code_file: "Writing code",
    organize_folder: "Organising files", arrange_desktop_icons: "Arranging desktop icons", click: "Clicking", close_tab: "Closing the tab" })) {
    assert.equal(L.toolLabel(tool), label);
  }
  assert.deepEqual(L.replyChips("Moved 89 files. 32 University. Say 'undo' to put them back."), [{ label: "Undo", say: "undo" }]);
  assert.deepEqual(L.replyChips("Arranged 32 desktop icons. Say 'restore desktop layout' to undo."), [{ label: "Restore layout", say: "restore desktop layout" }]);
  assert.deepEqual(L.replyChips("Opening youtube."), []);
  assert.deepEqual(L.replyChips(undefined), []);
  assert.equal(L.actionLabel("organize_folder"), "organise a folder (files will be moved)");
  assert.equal(L.actionLabel("request_control"), "control your mouse and keyboard");
  assert.equal(L.titleFrom("  hello   world "), "hello world");
  assert.equal(L.titleFrom(""), "New chat");
  assert.ok(L.titleFrom("x".repeat(100)).length <= 42);
  assert.equal(L.titleFrom("آواز کم کرو"), "آواز کم کرو");
});

test("groupLabel buckets", () => {
  const now = new Date(2026, 10, 13, 15, 0, 0).getTime(), day = 86400000;
  assert.equal(L.groupLabel(now - 3600000, now), "Today");
  assert.equal(L.groupLabel(now - day, now), "Yesterday");
  assert.equal(L.groupLabel(now - 3 * day, now), "Previous 7 days");
  assert.equal(L.groupLabel(now - 30 * day, now), "Older");
});

test("trimChats: drops empties, caps, keeps current", () => {
  const mk = (id, ts, n) => ({ id, ts, msgs: Array.from({ length: n }, () => ({})) });
  const list = [mk("a", 1, 2), mk("empty", 5, 0), mk("cur", 3, 0), mk("big", 2, 500)];
  const out = L.trimChats(list, "cur");
  assert.deepEqual(out.map((c) => c.id), ["cur", "big", "a"]);
  assert.equal(out.find((c) => c.id === "big").msgs.length, L.MAX_MSGS);
  const many = Array.from({ length: 50 }, (_, i) => mk("c" + i, i, 1));
  assert.equal(L.trimChats(many, "none").length, L.MAX_CHATS);
});

test("meters", () => {
  assert.equal(L.clamp01(2), 1); assert.equal(L.clamp01(-1), 0); assert.equal(L.clamp01("x"), 0);
  assert.equal(L.meterClass(0.5), ""); assert.equal(L.meterClass(0.85), "warn"); assert.equal(L.meterClass(1), "full");
});

test("markdown: structure", () => {
  const b = M.parse("# Title\n\nHello **bold** and `code` and *it*\nnext line\n\n- a\n- b\n\n1. one\n2) two\n\n```js\nlet x = 1;\n```");
  assert.deepEqual(b.map((x) => x.t), ["h", "p", "ul", "ol", "code"]);
  assert.deepEqual(b[1].inl.filter((n) => n.t !== "text").map((n) => n.t), ["b", "code", "i", "br"]);
  assert.equal(b[2].items.length, 2);
  assert.equal(b[4].lang, "js");
  assert.equal(b[4].text, "let x = 1;");
});

test("markdown: unclosed fence while streaming still renders as code", () => {
  const b = M.parse("Intro\n\n```\nCtrl+I\nmore");
  assert.deepEqual(b.map((x) => x.t), ["p", "code"]);
  assert.equal(b[1].text, "Ctrl+I\nmore");
});

test("markdown: HTML and links are never interpreted", () => {
  const src = '<img src=x onerror=alert(1)> <script>alert(1)</script> [x](javascript:alert(1))';
  const b = M.parse(src);
  assert.equal(b.length, 1);
  const all = b[0].inl.map((n) => n.v).join("");
  assert.equal(all, src);                     // kept verbatim as TEXT; the renderer only ever uses textContent
  assert.ok(b[0].inl.every((n) => n.t === "text"));
});

test("markdown: Urdu and mixed text survive; empty input", () => {
  assert.equal(M.parse("آواز **کم** ہو گئی")[0].inl[1].t, "b");
  assert.deepEqual(M.parse(""), []);
  assert.deepEqual(M.parse(null), []);
});
