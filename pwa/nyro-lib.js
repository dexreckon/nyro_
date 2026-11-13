"use strict";
/* Pure helpers (no DOM). Works in the browser (window.NyroLib) and in Node (module.exports) for tests. */
(function (root, factory) {
  const lib = factory();
  if (typeof module === "object" && module.exports) module.exports = lib; else root.NyroLib = lib;
})(typeof self !== "undefined" ? self : this, function () {
  const PRIVATE_HTTP = /^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$/;

  /** Turn whatever the user typed into a clean origin. http:// is only accepted for local/LAN dev hosts,
   *  and only when opts.allowLocalHttp is true (the Android app blocks cleartext traffic entirely). */
  function normalizeServer(input, opts) {
    const allowLocalHttp = !opts || opts.allowLocalHttp !== false;
    let s = String(input || "").trim();
    if (!s) return { ok: false, error: "Enter your server address." };
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = "https://" + s;
    let u;
    try { u = new URL(s); } catch { return { ok: false, error: "That address doesn't look right." }; }
    if (u.protocol === "wss:") u = new URL("https://" + u.host);
    if (u.protocol === "ws:") u = new URL("http://" + u.host);
    if (u.protocol !== "https:" && u.protocol !== "http:") return { ok: false, error: "Use an https:// address." };
    if (u.protocol === "http:" && !allowLocalHttp) return { ok: false, error: "Use an https:// address." };
    if (u.protocol === "http:" && !PRIVATE_HTTP.test(u.hostname)) return { ok: false, error: "Use https:// (plain http is only allowed on your local network)." };
    if (u.username || u.password) return { ok: false, error: "Remove the username/password from the address." };
    return { ok: true, origin: u.origin };
  }

  function wsUrl(origin, role) {
    const u = new URL(origin);
    return (u.protocol === "https:" ? "wss://" : "ws://") + u.host + "/ws/" + (role || "mobile");
  }

  const ACTIONS = {
    write_markdown: "write a note", soft_delete_file: "move a file to the trash",
    click_element: "click a button in your browser", type_text: "type into a web page", open_url: "open a web page",
  };
  const actionLabel = (a) => ACTIONS[a] || String(a || "do something").replace(/_/g, " ");

  const TOOLS = {
    capture_foreground_window: "Looking at your screen", render_screen_annotations: "Guiding you on screen",
    clear_annotations: "Clearing the overlay", set_volume: "Setting volume", set_brightness: "Setting brightness",
    toggle_mute: "Toggling mute", manage_window: "Arranging windows", lock_workstation: "Locking the PC",
    get_system_telemetry: "Checking system stats", open_url: "Opening a page", click_element: "Clicking in the browser",
    type_text: "Typing in the browser", extract_dom_text: "Reading the page", capture_tab_screenshot: "Looking at the tab",
    list_allowed_files: "Browsing your notes", read_document: "Reading a file", write_markdown: "Writing a note",
    soft_delete_file: "Moving a file to trash",
  };
  const toolLabel = (n) => TOOLS[n] || String(n || "tool").replace(/_/g, " ");

  function titleFrom(text) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    return t.length > 42 ? t.slice(0, 41).trimEnd() + "…" : (t || "New chat");
  }

  function groupLabel(ts, now) {
    const d0 = new Date(now); d0.setHours(0, 0, 0, 0);
    const day = 86400000, t = Number(ts) || 0;
    if (t >= d0.getTime()) return "Today";
    if (t >= d0.getTime() - day) return "Yesterday";
    if (t >= d0.getTime() - 7 * day) return "Previous 7 days";
    return "Older";
  }

  const MAX_CHATS = 30, MAX_MSGS = 200;
  /** Drop empty chats (except the current one), cap counts, newest first. */
  function trimChats(list, currentId) {
    const keep = list.filter((c) => c.id === currentId || (c.msgs && c.msgs.length));
    keep.sort((a, b) => b.ts - a.ts);
    keep.length = Math.min(keep.length, MAX_CHATS);
    keep.forEach((c) => { if (c.msgs.length > MAX_MSGS) c.msgs = c.msgs.slice(-MAX_MSGS); });
    return keep;
  }

  const clamp01 = (n) => Math.max(0, Math.min(1, Number(n) || 0));
  const meterClass = (f) => (f >= 1 ? "full" : f >= 0.8 ? "warn" : "");

  return { normalizeServer, wsUrl, actionLabel, toolLabel, titleFrom, groupLabel, trimChats, clamp01, meterClass, MAX_CHATS, MAX_MSGS };
});
