"use strict";
/* Speech-to-text adapter with three routes behind one small interface:
   - device:  Browser Web Speech API, or the Capacitor SpeechRecognition plugin in the Android app
   - laptop:  record the microphone here (MediaRecorder) and let the laptop's Whisper model transcribe it.
              Works in the Android app even when the phone has no speech service, and handles Urdu well. */
(function () {
  const cap = window.Capacitor;
  const isNative = !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
  const WebSR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let native = null, nativeHandlers = [], web = null, rec = null;

  function nativePlugin() {
    if (!native) native = cap.registerPlugin("SpeechRecognition");
    return native;
  }

  async function available() {
    if (isNative) { try { return !!(await nativePlugin().available()).available; } catch { return false; } }
    return !!WebSR;
  }

  /* ---------------------------------------------------------------- laptop route */
  function canRecord() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  }

  function pickMime() {
    for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]) {
      try { if (MediaRecorder.isTypeSupported(m)) return m; } catch { /* ignore */ }
    }
    return "";
  }

  /* o: { transcribe(b64, mime) -> Promise<string>, onTranscribing(), onEnd(text), onError(message), onLevel(rms) } */
  async function startRecording(o) {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch { o.onError("Microphone permission is off. Allow it for Nyro in your phone's settings."); return; }
    const mime = pickMime();
    let mr;
    try { mr = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 32000 } : undefined); }
    catch { stream.getTracks().forEach((t) => t.stop()); o.onError("This device can't record audio."); return; }

    const chunks = [];
    let spoke = false, silentSince = 0, timer = 0, ctx = null;
    const started = Date.now();
    const cleanup = () => {
      clearInterval(timer);
      try { stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
      try { if (ctx) ctx.close(); } catch { /* ignore */ }
      rec = null;
    };
    mr.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    mr.onstop = async () => {
      cleanup();
      if (!spoke) { o.onEnd(""); return; }
      const blob = new Blob(chunks, { type: mr.mimeType || mime || "audio/webm" });
      if (blob.size > 280000) { o.onError("That recording is too long. Try a shorter request."); return; }
      if (o.onTranscribing) o.onTranscribing();
      try {
        const b64 = await new Promise((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(String(r.result).split(",")[1] || "");
          r.onerror = () => rej(r.error);
          r.readAsDataURL(blob);
        });
        o.onEnd(await o.transcribe(b64, blob.type));
      } catch (e) { o.onError(String((e && e.message) || e || "Could not transcribe the recording.")); }
    };

    const stopNow = () => { try { if (mr.state !== "inactive") mr.stop(); } catch { /* ignore */ } };
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      ctx = new AC();
      if (ctx.resume) ctx.resume();
      const an = ctx.createAnalyser(); an.fftSize = 1024;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Float32Array(an.fftSize);
      timer = setInterval(() => {
        an.getFloatTimeDomainData(buf);
        let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
        const rms = Math.sqrt(s / buf.length), now = Date.now();
        if (rms > 0.02) { spoke = true; silentSince = 0; } else if (spoke && !silentSince) silentSince = now;
        if (o.onLevel) o.onLevel(rms);
        // stop 1.4 s after the speaker goes quiet, after 7 s of nobody speaking, or at 25 s
        if ((spoke && silentSince && now - silentSince > 1400) || (!spoke && now - started > 7000) || now - started > 25000) stopNow();
      }, 100);
    } catch { spoke = true; /* no level meter available: the user taps stop */ }
    rec = mr;
    mr.start(250);
  }

  /* ---------------------------------------------------------------- device route */
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
    if (rec) { try { if (rec.state !== "inactive") rec.stop(); } catch { /* ignore */ } return; }
    if (isNative) { try { await nativePlugin().stop(); } catch { /* ignore */ } return; }
    if (web) { try { web.stop(); } catch { /* ignore */ } }
  }

  window.NyroSpeech = { isNative, available, start, stop, canRecord, startRecording };
})();
