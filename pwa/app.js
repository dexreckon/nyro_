"use strict";
/* Nyro chat UI. All dynamic text goes through textContent / createTextNode: relayed text can never inject markup. */
(() => {
  const L = window.NyroLib, MD = window.NyroMD, SP = window.NyroSpeech;
  const $ = (id) => document.getElementById(id);
  const VERSION = "1.0.0";
  const APPROVAL_SECONDS = 120;
  const SVGNS = "http://www.w3.org/2000/svg";
  const PERSONA = { screencoach: "Screen coach", sysop: "System", webpilot: "Web", fileclerk: "Files" };

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage full / private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
  };
  const native = SP.isNative;
  const serverOrigin = () => (native ? store.get("nyro_server") : location.origin);
  // The Android app blocks cleartext traffic, so http:// servers are only offered with a developer flag.
  const normServer = (v) => L.normalizeServer(v, { allowLocalHttp: !native || store.get("nyro_dev_http") === "1" });

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function icon(name, cls) {
    const s = document.createElementNS(SVGNS, "svg"); s.setAttribute("class", "ic" + (cls ? " " + cls : ""));
    const u = document.createElementNS(SVGNS, "use"); u.setAttribute("href", "#i-" + name); s.appendChild(u); return s;
  }

  /* ------------------------------------------------------------ state */
  let ws = null, backoff = 1000, retryT = null, everConnected = false, hostOnline = false, connState = "connecting";
  let lang = store.get("nyro_lang") === "ur" ? "ur" : "en";
  let curAi = null, latestShot = null, listening = false, awaitingReply = 0;
  const pendingHeard = [];
  const approvals = [];
  let stick = true;

  /* ------------------------------------------------------------ chats (local, per device) */
  let data = (() => { try { const d = JSON.parse(store.get("nyro_chats") || "null"); if (d && Array.isArray(d.chats)) return d; } catch { /* corrupt */ } return { chats: [], current: null }; })();
  let saveT = null;
  const saveSoon = () => { clearTimeout(saveT); saveT = setTimeout(save, 400); };
  function save() {
    data.chats = L.trimChats(data.chats, data.current);
    store.set("nyro_chats", JSON.stringify(data, (k, v) => (k[0] === "_" ? undefined : v)));
  }
  function newChatObj() { return { id: "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), title: "New chat", ts: Date.now(), msgs: [] }; }
  function chat() {
    let c = data.chats.find((x) => x.id === data.current);
    if (!c) { c = newChatObj(); data.chats.unshift(c); data.current = c.id; }
    return c;
  }

  /* ------------------------------------------------------------ rendering */
  const thread = $("thread"), scrollEl = $("scroll"), input = $("input");
  const nearBottom = () => scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 140;
  const toBottom = (force) => { if (force || stick) scrollEl.scrollTop = scrollEl.scrollHeight; };

  function mount(msg) {
    stick = nearBottom();
    const v = (msg._v = {});
    if (msg.role === "user") {
      const row = el("div", "row user"); const b = el("div", "bubble");
      if (msg.voice) b.appendChild(el("span", "mic-tag", "Spoken on laptop"));
      b.appendChild(document.createTextNode(msg.text || "")); b.dir = "auto"; row.appendChild(b); v.row = row;
    } else if (msg.role === "note") {
      v.row = el("div", "note" + (msg.kind ? " " + msg.kind : ""), msg.text); v.row.dir = "auto";
    } else {
      const row = el("div", "row ai"); const av = el("div", "avatar"); av.appendChild(icon("logo"));
      const body = el("div", "ai-body"); v.shot = el("div"); v.steps = el("div", "steps"); v.content = el("div", "content");
      v.actions = el("div", "msg-actions"); const cp = el("button"); cp.type = "button"; cp.setAttribute("aria-label", "Copy"); cp.appendChild(icon("copy"));
      cp.addEventListener("click", () => copyText(msg.text || "", cp)); v.actions.appendChild(cp);
      body.append(v.shot, v.steps, v.content, v.actions); row.append(av, body); v.row = row; v.copyBtn = cp;
    }
    thread.appendChild(v.row);
    if (msg.role === "ai") updateAi(msg);
    toBottom();
    return msg;
  }

  function updateAi(msg) {
    const v = msg._v; if (!v) return;
    stick = nearBottom();
    v.steps.textContent = "";
    (msg.steps || []).forEach((s, i, arr) => v.steps.appendChild(el("span", "step" + (!msg.done && i === arr.length - 1 && !msg.text ? " live" : ""), s)));
    if (msg.text) MD.render(document, v.content, msg.text);
    else if (!msg.done) { v.content.textContent = ""; const t = el("div", "typing"); t.append(el("i"), el("i"), el("i")); t.setAttribute("aria-label", "Nyro is thinking"); v.content.appendChild(t); }
    else v.content.textContent = "";
    v.actions.hidden = !(msg.done && msg.text);
    toBottom();
  }

  function addMsg(msg) {
    const c = chat();
    c.msgs.push(msg); c.ts = Date.now();
    if (msg.role === "user" && c.title === "New chat") { c.title = L.titleFrom(msg.text); renderList(); setTitle(); }
    mount(msg); updateEmpty(); saveSoon();
    return msg;
  }
  const note = (text, kind) => addMsg({ role: "note", text, kind });

  function renderChat() {
    const c = chat();
    thread.querySelectorAll(":scope > :not(#empty)").forEach((n) => n.remove());
    curAi = null;
    c.msgs.forEach((m) => { if (m.role === "ai") m.done = true; mount(m); });
    updateEmpty(); setTitle(); renderList(); toBottom(true);
  }
  const updateEmpty = () => { $("empty").hidden = chat().msgs.length > 0; };
  function setTitle() { const c = chat(); $("title").textContent = c.msgs.length ? c.title : "Nyro"; }

  function renderList() {
    const box = $("chatList"); box.textContent = ""; let lastG = "";
    L.trimChats(data.chats, data.current).forEach((c) => {
      if (!c.msgs.length) return;
      const g = L.groupLabel(c.ts, Date.now());
      if (g !== lastG) { box.appendChild(el("div", "group", g)); lastG = g; }
      const b = el("button", "chat-item", c.title); b.type = "button"; b.dir = "auto";
      if (c.id === data.current) b.setAttribute("aria-current", "true");
      b.addEventListener("click", () => { data.current = c.id; renderChat(); closeSide(); save(); });
      box.appendChild(b);
    });
  }
  function startNewChat() {
    if (!chat().msgs.length) { closeSide(); input.focus(); return; }
    const c = newChatObj(); data.chats.unshift(c); data.current = c.id; renderChat(); closeSide(); save();
  }

  async function copyText(text, btn) {
    try { await navigator.clipboard.writeText(text); } catch {
      const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch { /* ignore */ } t.remove();
    }
    btn.textContent = ""; btn.appendChild(icon("check"));
    setTimeout(() => { btn.textContent = ""; btn.appendChild(icon("copy")); }, 1400);
  }

  /* ------------------------------------------------------------ sidebar */
  const sidebar = $("sidebar"), scrim = $("scrim");
  const sideOpen = () => sidebar.classList.contains("open");
  function openSide() { sidebar.classList.add("open"); scrim.hidden = false; }
  function closeSide() { sidebar.classList.remove("open"); scrim.hidden = true; }
  $("btnMenu").addEventListener("click", openSide); $("btnCloseSide").addEventListener("click", closeSide); scrim.addEventListener("click", closeSide);
  $("btnNew").addEventListener("click", startNewChat); $("btnNewSide").addEventListener("click", startNewChat);

  /* ------------------------------------------------------------ status */
  function setBar(barId, textId, frac, label) {
    const f = L.clamp01(frac); const b = $(barId); b.style.width = f * 100 + "%"; b.className = L.meterClass(f); $(textId).textContent = label;
  }
  const setBudget = (used, cap) => { if (cap) setBar("budgetBar", "budgetText", used / cap, `${used} / ${cap}`); };
  const setUsage = (f) => { if (typeof f === "number") setBar("usageBar", "usageText", f, Math.round(f * 100) + "%"); };

  function renderStatus() {
    const online = connState === "online" && hostOnline;
    $("topDot").className = "dot " + (online ? "" : connState === "online" ? "off" : "warn");
    $("sbDot").className = $("topDot").className;
    $("sbStatus").textContent = connState !== "online" ? ({ connecting: "Connecting…", reconnecting: "Reconnecting…", unreachable: "Can't reach server", locked: "Too many attempts" }[connState] || "Offline") : online ? "Laptop online" : "Laptop offline";
    const bn = $("banner"); bn.textContent = "";
    let text = "", kind = "";
    if (connState === "unreachable") { text = "Can't reach your server. Retrying…"; kind = "err"; }
    else if (connState === "locked") { text = "Too many failed attempts. Wait a few minutes."; kind = "err"; }
    else if (connState === "reconnecting") { text = "Connection lost. Reconnecting…"; kind = "warn"; }
    else if (connState === "online" && !hostOnline) { text = "Your laptop is offline. Start Nyro on it to send commands."; kind = "warn"; }
    bn.hidden = !text; bn.className = kind;
    if (text) {
      bn.appendChild(el("span", "", text));
      if (connState === "unreachable" || connState === "locked") { const b = el("button", "", "Settings"); b.type = "button"; b.addEventListener("click", openSettings); bn.appendChild(b); }
    }
  }
  const setConn = (s) => { connState = s; renderStatus(); };
  const setHost = (on) => { hostOnline = !!on; if (!on) awaitingReply = 0; renderStatus(); };

  /* ------------------------------------------------------------ socket */
  const send = (o) => { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(o)); return true; } return false; };

  /* Voice recorded on the phone is transcribed by the laptop (Whisper). Replies arrive as {type:"transcript", rid}. */
  const pendingTx = {};
  let txId = 1;
  function transcribeOnLaptop(b64, mime) {
    return new Promise((resolve, reject) => {
      if (connState !== "online" || !hostOnline) { reject(new Error("Your laptop is offline.")); return; }
      const rid = txId++;
      const timer = setTimeout(() => { delete pendingTx[rid]; reject(new Error("The laptop took too long to transcribe. Try again.")); }, 90000);
      pendingTx[rid] = { resolve, reject, timer };
      if (!send({ type: "audio", b64, mime, rid, lang })) { clearTimeout(timer); delete pendingTx[rid]; reject(new Error("Not connected.")); }
    });
  }

  function connect() {
    clearTimeout(retryT);
    const token = store.get("nyro_token"), origin = serverOrigin();
    if (!token || !origin) { showOnboard(); return; }
    showApp();
    let sock;
    try { sock = new WebSocket(L.wsUrl(origin)); } catch { setConn("unreachable"); retryT = setTimeout(connect, 5000); return; }
    ws = sock; setConn(everConnected ? "reconnecting" : "connecting");
    sock.onopen = () => sock.send(JSON.stringify({ type: "auth", token }));
    sock.onmessage = (e) => { if (sock !== ws) return; try { onMsg(JSON.parse(e.data)); } catch { /* ignore malformed */ } };
    sock.onerror = () => {};
    sock.onclose = (ev) => {
      if (sock !== ws) return;
      ws = null; setHost(false);
      if (ev.code === 4401) { store.del("nyro_token"); showOnboard("That token was not accepted. Check it and try again."); return; }
      setConn(ev.code === 4429 ? "locked" : everConnected ? "reconnecting" : "unreachable");
      retryT = setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15000);
    };
  }
  function disconnect() { clearTimeout(retryT); const s = ws; ws = null; if (s) try { s.close(); } catch { /* ignore */ } }

  function onMsg(m) {
    switch (m.type) {
      case "auth_ok": backoff = 1000; everConnected = true; setConn("online"); break;
      case "snapshot":
        setHost(m.host_online);
        if (!chat().msgs.length) (m.history || []).forEach(onMsg);
        if (m.screenshot) showShot(m.screenshot.b64, m.screenshot.mime);
        (m.pending_approvals || []).forEach(queueApproval);
        if (m.host_online) send({ type: "sync" });
        break;
      case "status": setHost(m.host_online); break;
      case "heard": {
        const i = pendingHeard.indexOf(m.text);
        if (i >= 0) pendingHeard.splice(i, 1); else addMsg({ role: "user", text: String(m.text || ""), voice: true });
        curAi = null; break;
      }
      case "transcript": {
        const p = pendingTx[m.rid]; if (!p) break;
        clearTimeout(p.timer); delete pendingTx[m.rid];
        if (m.error) p.reject(new Error(String(m.error))); else p.resolve(String(m.text || ""));
        break;
      }
      case "screenshot": showShot(m.b64, m.mime); break;
      case "approval_request": queueApproval(m); break;
      case "approval_resolved": { const i = approvals.findIndex((a) => a.id === m.id); if (i >= 0) { approvals.splice(i, 1); showApproval(); } break; }
      case "approval_expired": note("That approval expired.", "warn"); break;
      case "reply": finishReply(m); break;
      case "error": awaitingReply = 0; note(String(m.error || "Something went wrong."), "err"); break;
      case "event": onEvent(m); break;
      default: break;
    }
  }

  function ensureAi(persona) {
    if (!curAi) curAi = addMsg({ role: "ai", persona: persona || "", text: "", steps: [], done: false });
    return curAi;
  }
  function onEvent(e) {
    switch (e.event) {
      case "start": curAi = null; ensureAi(PERSONA[e.persona] || ""); { const p = $("persona"); p.textContent = PERSONA[e.persona] || ""; p.hidden = !p.textContent; } break;
      case "text": { const a = ensureAi(); a.text = (a.text || "") + (e.text || ""); updateAi(a); break; }
      case "tool": { const a = ensureAi(); (a.steps = a.steps || []).push(L.toolLabel(e.name)); updateAi(a); break; }
      case "budget": setBudget(e.used, e.cap);
        if (e.exhausted) note("Daily AI limit reached. Local controls still work.", "warn"); else if (e.pace_warn) note("You're using AI requests quickly.", "warn"); break;
      case "usage": setUsage(e.five_hour); break;
      case "error": awaitingReply = 0; if (curAi) { curAi.done = true; updateAi(curAi); curAi = null; } note(String(e.error || "Something went wrong."), "err"); break;
      default: break;
    }
  }
  function finishReply(m) {
    awaitingReply = 0;
    const a = curAi || ensureAi(m.source === "local" ? "Laptop" : PERSONA[m.persona] || "");
    if (typeof m.text === "string" && m.text) a.text = m.text;
    a.done = true; updateAi(a); curAi = null; saveSoon();
    if (m.source === "limit") note("Daily AI limit reached. Local controls still work.", "warn");
  }
  function showShot(b64, mime) {
    if (typeof b64 !== "string" || !/^[A-Za-z0-9+/=]+$/.test(b64)) return;
    latestShot = `data:${mime === "image/webp" ? "image/webp" : "image/png"};base64,${b64}`;
    if (curAi && curAi._v && !curAi._v.shot.firstChild) {
      const a = el("button", "shot-thumb"); a.type = "button"; a.setAttribute("aria-label", "View screenshot");
      const img = document.createElement("img"); img.src = latestShot; img.alt = "What Nyro sees"; a.appendChild(img);
      a.addEventListener("click", openShot); curAi._v.shot.appendChild(a); toBottom();
    }
  }

  /* ------------------------------------------------------------ sending */
  function sendText(text) {
    text = (text || "").trim(); if (!text) return false;
    if (connState !== "online" || !ws) { note("Not connected to your server yet.", "warn"); return false; }
    if (!hostOnline) { note("Your laptop is offline.", "warn"); return false; }
    if (awaitingReply && Date.now() - awaitingReply < 120000) { note("Nyro is still working on your last request.", "warn"); return false; }
    if (!send({ type: "command", text, lang })) { note("Message not sent. Reconnecting…", "err"); return false; }
    awaitingReply = Date.now(); pendingHeard.push(text); curAi = null;
    addMsg({ role: "user", text });
    return true;
  }
  const autosize = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 168) + "px"; };
  const refreshSend = () => { $("btnSend").disabled = !input.value.trim(); };
  input.addEventListener("input", () => { autosize(); refreshSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !matchMedia("(pointer: coarse)").matches) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault(); const t = input.value;
    if (sendText(t)) { input.value = ""; autosize(); refreshSend(); }
  });
  $("suggestions").addEventListener("click", (e) => { const b = e.target.closest("button[data-say]"); if (b) sendText(b.dataset.say); });

  /* ------------------------------------------------------------ voice */
  function setLang(l) {
    lang = l === "ur" ? "ur" : "en"; store.set("nyro_lang", lang);
    $("btnLang").textContent = lang === "ur" ? "اردو" : "EN"; $("stLang").value = lang; input.dir = "auto";
  }
  $("btnLang").addEventListener("click", () => setLang(lang === "en" ? "ur" : "en"));
  function micUi(on) {
    listening = on; const b = $("btnMic"); b.classList.toggle("live", on); b.setAttribute("aria-label", on ? "Stop dictation" : "Dictate");
    b.textContent = ""; b.appendChild(icon(on ? "stop" : "mic")); input.placeholder = on ? "Listening…" : "Message Nyro";
  }
  $("btnMic").addEventListener("click", async () => {
    if (listening) { SP.stop(); return; }
    // In the Android app (and browsers without speech recognition) record here and let the laptop transcribe.
    if (SP.canRecord() && (SP.isNative || !(await SP.available()))) {
      if (connState !== "online" || !hostOnline) { note("Voice needs your laptop to be online.", "warn"); return; }
      micUi(true);
      SP.startRecording({
        transcribe: transcribeOnLaptop,
        onTranscribing: () => { input.placeholder = "Transcribing on your laptop…"; },
        onEnd: (t) => {
          micUi(false); const v = (t || "").trim();
          if (!v) { note("I didn't catch that. Tap the mic and try again.", "warn"); return; }
          if (sendText(v)) { input.value = ""; autosize(); refreshSend(); } else { input.value = v; autosize(); refreshSend(); }
        },
        onError: (msg) => { micUi(false); note(msg, "err"); },
      });
      return;
    }
    if (!(await SP.available())) { note("Voice input isn't available on this device. You can still type.", "warn"); return; }
    micUi(true);
    SP.start({
      lang: lang === "ur" ? "ur-PK" : "en-US",
      onPartial: (t) => { input.value = t; autosize(); refreshSend(); },
      onEnd: (t) => { micUi(false); const v = (t || input.value).trim(); if (v && sendText(v)) { input.value = ""; autosize(); refreshSend(); } },
      onError: (msg) => { micUi(false); note(msg, "err"); },
    });
  });

  /* ------------------------------------------------------------ approvals */
  let apTimer = null;
  function showApproval() {
    const dlg = $("approval"), cur = approvals[0];
    clearInterval(apTimer);
    if (!cur) { if (dlg.open) dlg.close(); return; }
    $("apTitle").textContent = "Nyro wants to " + L.actionLabel(cur.action);
    $("apSummary").textContent = cur.summary || cur.action; $("apSummary").dir = "auto"; $("apAction").textContent = cur.action;
    if (!dlg.open) { dlg.showModal(); if (navigator.vibrate && navigator.userActivation && navigator.userActivation.hasBeenActive) navigator.vibrate([180, 90, 180]); }
    const tick = () => {
      const left = Math.max(0, (cur.deadline - Date.now()) / 1000);
      $("apTimer").textContent = Math.ceil(left) + "s left"; $("apBar").style.width = (left / APPROVAL_SECONDS) * 100 + "%";
      if (left <= 0) resolveApproval(cur.id, null);
    };
    tick(); apTimer = setInterval(tick, 500);
  }
  function resolveApproval(id, approved) {
    const i = approvals.findIndex((a) => a.id === id); if (i < 0) return;
    approvals.splice(i, 1);
    if (approved !== null) { send({ type: "approval_response", id, approved }); note(approved ? "You approved that action." : "You rejected that action.", approved ? "" : "warn"); }
    else note("An approval request timed out.", "warn");
    showApproval();
  }
  function queueApproval(m) {
    if (approvals.some((a) => a.id === m.id)) return;
    approvals.push({ id: m.id, action: String(m.action || ""), summary: String(m.summary || ""), deadline: Date.now() + APPROVAL_SECONDS * 1000 });
    showApproval();
  }
  $("apApprove").addEventListener("click", () => approvals[0] && resolveApproval(approvals[0].id, true));
  $("apReject").addEventListener("click", () => approvals[0] && resolveApproval(approvals[0].id, false));
  $("approval").addEventListener("cancel", (e) => e.preventDefault());

  /* ------------------------------------------------------------ dialogs: shot + settings */
  function openShot() {
    $("shotImg").hidden = !latestShot; $("shEmpty").hidden = !!latestShot; if (latestShot) $("shotImg").src = latestShot;
    $("shotDlg").showModal(); closeSide();
  }
  $("btnShot").addEventListener("click", openShot); $("shClose").addEventListener("click", () => $("shotDlg").close());

  function applyTheme(t) {
    t = ["light", "dark", "system"].includes(t) ? t : "system"; document.documentElement.dataset.theme = t; store.set("nyro_theme", t); $("stTheme").value = t;
    const bg = getComputedStyle(document.body).backgroundColor; const m = document.querySelector('meta[name="theme-color"]'); if (m && bg) m.setAttribute("content", bg);
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(store.get("nyro_theme") || "system"));
  $("stTheme").addEventListener("change", (e) => applyTheme(e.target.value));
  $("stLang").addEventListener("change", (e) => setLang(e.target.value));

  function openSettings() {
    $("stServerRow").hidden = !native; $("stServer").value = native ? store.get("nyro_server") || "" : "";
    $("stToken").value = ""; $("stError").textContent = ""; $("stVersion").textContent = `Nyro ${VERSION}${native ? " (Android)" : ""} · ${native ? serverOrigin() || "" : location.host}`;
    $("settings").showModal(); closeSide();
  }
  $("btnSettings").addEventListener("click", openSettings); $("stClose").addEventListener("click", () => $("settings").close());
  $("stSave").addEventListener("click", () => {
    let changed = false;
    if (native) {
      const r = normServer($("stServer").value); if (!r.ok) { $("stError").textContent = r.error; return; }
      if (r.origin !== store.get("nyro_server")) { store.set("nyro_server", r.origin); changed = true; }
    }
    const tok = $("stToken").value.trim();
    if (tok) { if (tok.length < 24) { $("stError").textContent = "That token looks too short."; return; } store.set("nyro_token", tok); changed = true; }
    $("settings").close();
    if (changed) { disconnect(); everConnected = false; backoff = 1000; connect(); }
  });
  function armConfirm(btn, armedText, fn) {
    const orig = btn.textContent; let armed = false, t;
    btn.addEventListener("click", () => {
      if (!armed) { armed = true; btn.textContent = armedText; t = setTimeout(() => { armed = false; btn.textContent = orig; }, 3000); return; }
      clearTimeout(t); armed = false; btn.textContent = orig; fn();
    });
  }
  armConfirm($("stClear"), "Tap again to confirm", () => { data = { chats: [], current: null }; save(); renderChat(); $("settings").close(); });
  armConfirm($("stUnpair"), "Tap again to confirm", () => {
    store.del("nyro_token"); if (native) store.del("nyro_server"); disconnect(); everConnected = false; $("settings").close(); showOnboard();
  });

  /* ------------------------------------------------------------ onboarding */
  function showOnboard(err) {
    $("app").hidden = true; $("onboard").hidden = false; $("obServerRow").hidden = !native;
    $("obError").textContent = err || ""; if (native) $("obServer").value = store.get("nyro_server") || ""; $("obToken").value = "";
  }
  function showApp() { $("onboard").hidden = true; $("app").hidden = false; renderStatus(); }
  $("obForm").addEventListener("submit", (e) => {
    e.preventDefault();
    if (native) { const r = normServer($("obServer").value); if (!r.ok) { $("obError").textContent = r.error; return; } store.set("nyro_server", r.origin); }
    const t = $("obToken").value.trim(); if (t.length < 24) { $("obError").textContent = "That token looks too short."; return; }
    store.set("nyro_token", t); $("obError").textContent = ""; backoff = 1000; everConnected = false; connect();
  });

  /* ------------------------------------------------------------ Android back button */
  if (native && window.Capacitor.registerPlugin) {
    try {
      const AppPlugin = window.Capacitor.registerPlugin("App");
      AppPlugin.addListener("backButton", () => {
        const open = document.querySelector("dialog[open]");
        if (open && open.id !== "approval") open.close();
        else if (sideOpen()) closeSide();
        else if (chat().msgs.length) startNewChat();
        else AppPlugin.exitApp();
      });
    } catch { /* plugin missing: default back behaviour */ }
  }

  /* ------------------------------------------------------------ boot */
  applyTheme(store.get("nyro_theme") || "system"); setLang(lang); micUi(false); refreshSend();
  renderChat();
  if (!native && "serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  connect();
})();
