"use strict";
/* Speech-to-text adapter. Browser: Web Speech API. Android app: Capacitor SpeechRecognition plugin
   (WebView has no Web Speech API). Same interface for both. */
(function () {
  const cap = window.Capacitor;
  const isNative = !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
  const WebSR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let native = null, nativeHandlers = [], web = null;

  function nativePlugin() {
    if (!native) native = cap.registerPlugin("SpeechRecognition");
    return native;
  }

  async function available() {
    if (isNative) { try { return !!(await nativePlugin().available()).available; } catch { return false; } }
    return !!WebSR;
  }

  async function start(o) {
    // o: { lang, onPartial(text), onEnd(finalText), onError(message) }
    if (isNative) {
      const p = nativePlugin();
      let last = "", ended = false;
      const finish = () => { if (ended) return; ended = true; cleanup(); o.onEnd(last); };
      const cleanup = () => { nativeHandlers.forEach((h) => { try { h.remove(); } catch { /* ignore */ } }); nativeHandlers = []; };
      try {
        let perm = await p.checkPermissions();
        if (perm.speechRecognition !== "granted") perm = await p.requestPermissions();
        if (perm.speechRecognition !== "granted") { o.onError("Microphone permission is off. Enable it in Android settings."); return; }
        nativeHandlers.push(await p.addListener("partialResults", (d) => { if (d && d.matches && d.matches[0]) { last = d.matches[0]; o.onPartial(last); } }));
        nativeHandlers.push(await p.addListener("listeningState", (d) => { if (d && d.status === "stopped") finish(); }));
        await p.start({ language: o.lang, maxResults: 1, partialResults: true, popup: false });
      } catch (e) { cleanup(); ended = true; o.onError(String((e && e.message) || e || "Speech recognition failed")); }
      return;
    }
    if (!WebSR) { o.onError("Voice input isn't supported in this browser."); return; }
    let finalText = "", done = false;
    web = new WebSR();
    web.lang = o.lang; web.interimResults = true; web.continuous = false;
    web.onresult = (ev) => {
      let fin = "", interim = "";
      for (const r of ev.results) { if (r.isFinal) fin += r[0].transcript; else interim += r[0].transcript; }
      finalText = (fin + interim).trim(); o.onPartial(finalText);
    };
    web.onerror = (ev) => { if (ev.error !== "no-speech" && ev.error !== "aborted") o.onError("Mic error: " + ev.error); };
    web.onend = () => { if (done) return; done = true; web = null; o.onEnd(finalText); };
    try { web.start(); } catch (e) { web = null; o.onError("Could not start the microphone."); }
  }

  async function stop() {
    if (isNative) { try { await nativePlugin().stop(); } catch { /* ignore */ } return; }
    if (web) { try { web.stop(); } catch { /* ignore */ } }
  }

  window.NyroSpeech = { isNative, available, start, stop };
})();
