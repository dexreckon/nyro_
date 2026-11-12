"use strict";
/* Nyro mobile remote. All text is inserted with textContent (never innerHTML). */
(() => {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
  };
  const MAX_NODES = 200;
  const APPROVAL_SECONDS = 120;

  let ws = null, backoff = 1000, lang = store.get("nyro_lang") === "ur" ? "ur" : "en";
  let curAi = null;            // streaming assistant bubble
  let hostOnline = false;
  const approvals = [];        // pending approval objects, first is shown

  // ---------- rendering helpers
  function add(cls, text, tag) {
    const d = document.createElement("div");
    d.className = "msg " + cls; d.dir = "auto";
    if (tag) { const t = document.createElement("span"); t.className = "tag"; t.textContent = tag; d.appendChild(t); }
    d.appendChild(document.createTextNode(text));
    const s = $("stream");
    s.appendChild(d);
    while (s.children.length > MAX_NODES) s.removeChild(s.firstChild);
    d.scrollIntoView({ block: "end", behavior: "smooth" });
    return d;
  }
  function setBar(barId, textId, frac, label) {
    const b = $(barId); b.style.width = Math.max(0, Math.min(1, frac)) * 100 + "%";
    b.className = frac >= 1 ? "full" : frac >= 0.8 ? "warn" : "";
    $(textId).textContent = label;
  }
  function setBudget(used, cap) { if (cap) setBar("budgetBar", "budgetText", used / cap, `AI ${used}/${cap}`); }
  function setUsage(frac) { if (typeof frac === "number") setBar("usageBar", "usageText", frac, `5h ${Math.round(frac * 100)}%`); }
  function setHost(online) {
    hostOnline = !!online;
    $("dot").className = "dot " + (online ? "" : "off");
    $("conn").textContent = online ? "Laptop online" : "Laptop offline";
  }
  function showShot(b64, mime) {
    if (typeof b64 !== "string" || !/^[A-Za-z0-9+/=]+$/.test(b64)) return;
    $("shot").src = `data:${mime === "image/webp" ? "image/webp" : "image/png"};base64,${b64}`;
    $("shotWrap").hidden = false;
  }

  // ---------- approvals
  let apTimer = null, apDeadline = 0;
  function showApproval() {
    const dlg = $("approval"), cur = approvals[0];
    if (!cur) { clearInterval(apTimer); if (dlg.open) dlg.close(); return; }
    $("apSummary").textContent = cur.summary || cur.action;
    $("apAction").textContent = cur.action;
    apDeadline = cur.deadline;
    if (!dlg.open) { dlg.showModal(); if (navigator.vibrate && navigator.userActivation && navigator.userActivation.hasBeenActive) navigator.vibrate([200, 100, 200]); }
    clearInterval(apTimer);
    const tick = () => {
      const left = Math.max(0, Math.round((apDeadline - Date.now()) / 1000));
      $("apTimer").textContent = left + "s left";
      if (left <= 0) { resolveApproval(cur.id, null); }
    };
    tick(); apTimer = setInterval(tick, 500);
  }
  function resolveApproval(id, approved) {
    const i = approvals.findIndex((a) => a.id === id);
    if (i < 0) return;
    approvals.splice(i, 1);
    if (approved !== null) send({ type: "approval_response", id, approved });
    showApproval();
  }
  function queueApproval(m) {
    if (approvals.some((a) => a.id === m.id)) return;
    approvals.push({ id: m.id, action: String(m.action || ""), summary: String(m.summary || ""), deadline: Date.now() + APPROVAL_SECONDS * 1000 });
    showApproval();
  }
  $("apApprove").onclick = () => approvals[0] && resolveApproval(approvals[0].id, true);
  $("apReject").onclick = () => approvals[0] && resolveApproval(approvals[0].id, false);
  $("approval").addEventListener("cancel", (e) => e.preventDefault());   // Esc must not silently dismiss

  // ---------- socket
  function send(obj) { if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; } return false; }
  function connect() {
    const token = store.get("nyro_token");
    if (!token) { $("pair").hidden = false; $("main").hidden = true; $("dock").hidden = true; return; }
    $("pair").hidden = true; $("main").hidden = false; $("dock").hidden = false;
    $("conn").textContent = "Connecting…";
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/mobile`);
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
    ws.onmessage = (e) => { try { onMsg(JSON.parse(e.data)); } catch { /* ignore malformed */ } };
    ws.onclose = (e) => {
      setHost(false);
      if (e.code === 4401) { store.del("nyro_token"); $("pairError").textContent = "Wrong token."; connect(); return; }
      if (e.code === 4429) { $("conn").textContent = "Too many attempts. Wait a few minutes."; }
      setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15000);
    };
  }

  function onMsg(m) {
    switch (m.type) {
      case "auth_ok": backoff = 1000; break;
      case "snapshot":
        setHost(m.host_online);
        $("stream").textContent = "";
        (m.history || []).forEach(onMsg);
        if (m.screenshot) showShot(m.screenshot.b64, m.screenshot.mime);
        (m.pending_approvals || []).forEach(queueApproval);
        if (m.host_online) send({ type: "sync" });
        break;
      case "status": setHost(m.host_online); break;
      case "heard": curAi = null; add("me", m.text || "", "You"); break;
      case "listening": add("tool", "Listening…"); break;
      case "idle": break;
      case "screenshot": showShot(m.b64, m.mime); break;
      case "approval_request": queueApproval(m); break;
      case "approval_resolved": { const i = approvals.findIndex((a) => a.id === m.id); if (i >= 0) { approvals.splice(i, 1); showApproval(); } break; }
      case "approval_expired": add("warn", "That approval expired.", "Approval"); break;
      case "reply":
        if (curAi) { curAi.lastChild.textContent = m.text || ""; curAi = null; }
        else add("ai", m.text || "", m.persona || "Nyro");
        if (m.source === "limit") add("warn", "Daily AI limit reached. Local controls still work.");
        break;
      case "error": add("err", m.error || "Error"); break;
      case "event": onEvent(m); break;
      default: break;
    }
  }
  function onEvent(e) {
    switch (e.event) {
      case "start": curAi = add("ai", "", e.persona || "Nyro"); break;
      case "text": if (!curAi) curAi = add("ai", "", "Nyro"); curAi.lastChild.textContent += e.text || ""; break;
      case "tool": add("tool", "Using " + String(e.name || "tool").replace(/_/g, " ")); break;
      case "budget": setBudget(e.used, e.cap); if (e.exhausted) add("warn", "Daily AI limit reached."); else if (e.pace_warn) add("warn", "Using requests quickly."); break;
      case "usage": setUsage(e.five_hour); break;
      case "error": add("err", String(e.error || "Error")); curAi = null; break;
      case "done": break;
      default: break;
    }
  }

  // ---------- commands
  function say(text) {
    text = (text || "").trim();
    if (!text) return;
    if (!hostOnline) { add("err", "Laptop is offline."); return; }
    if (!send({ type: "command", text, lang })) add("err", "Not connected.");
  }
  $("sayForm").addEventListener("submit", (e) => { e.preventDefault(); const i = $("say"); say(i.value); i.value = ""; });
  $("chips").addEventListener("click", (e) => { const b = e.target.closest("button[data-say]"); if (b) say(b.dataset.say); });
  $("pairForm").addEventListener("submit", (e) => {
    e.preventDefault(); const t = $("token").value.trim();
    if (t.length < 24) { $("pairError").textContent = "Token looks too short."; return; }
    store.set("nyro_token", t); $("token").value = ""; $("pairError").textContent = ""; connect();
  });

  // ---------- language + push-to-talk
  function setLang(l) {
    lang = l; store.set("nyro_lang", l);
    $("langEn").setAttribute("aria-pressed", String(l === "en")); $("langUr").setAttribute("aria-pressed", String(l === "ur"));
    $("say").dir = l === "ur" ? "rtl" : "auto";
  }
  $("langEn").onclick = () => setLang("en"); $("langUr").onclick = () => setLang("ur"); setLang(lang);

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $("mic");
  if (!SR) { mic.hidden = true; $("say").placeholder = "Type a command (voice not supported here)"; }
  else {
    let rec = null, finalText = "";
    const start = () => {
      if (rec) return;
      rec = new SR(); rec.lang = lang === "ur" ? "ur-PK" : "en-US"; rec.interimResults = true; rec.continuous = false; finalText = "";
      rec.onresult = (ev) => {
        let interim = ""; finalText = "";
        for (const r of ev.results) { if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript; }
        $("say").value = (finalText + interim).trim();
      };
      rec.onerror = (ev) => { if (ev.error !== "no-speech" && ev.error !== "aborted") add("err", "Mic error: " + ev.error); };
      rec.onend = () => { mic.classList.remove("live"); const t = ($("say").value || "").trim(); rec = null; if (t) { say(t); $("say").value = ""; } };
      try { rec.start(); mic.classList.add("live"); } catch { rec = null; }
    };
    const stop = () => { if (rec) try { rec.stop(); } catch { /* ignore */ } };
    mic.addEventListener("pointerdown", (e) => { e.preventDefault(); mic.setPointerCapture(e.pointerId); start(); });
    mic.addEventListener("pointerup", stop); mic.addEventListener("pointercancel", stop);
    mic.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  // ---------- boot
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  connect();
})();
