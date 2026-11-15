"""Nyro tray app: wake word / hotkey / phone -> STT -> orchestrator -> voice + overlay + phone."""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from host.approval import ApprovalBroker
from host.budget import RequestBudget
from host.config import load_config
from host.orchestrator import Orchestrator, Reply

ROOT = Path(__file__).resolve().parent.parent


class NyroApp:
    """Pure wiring. Every external piece is injectable so the pipeline is testable."""

    def __init__(self, cfg: dict[str, Any], data_dir: Path, orchestrator: Orchestrator,
                 record_and_transcribe: Callable[[], tuple[str, str]] | None = None,
                 speak: Callable[[str], Any] | None = None,
                 send_phone: Callable[[dict], None] | None = None,
                 beep: Callable[[], None] | None = None,
                 notify: Callable[[str, str], None] | None = None,
                 island: Any = None,
                 transcribe_bytes: Callable[[bytes, str | None], tuple[str, str]] | None = None,
                 stop_speech: Callable[[], None] | None = None):
        self._tb = transcribe_bytes
        self._stop_speech = stop_speech or (lambda: None)   # silence Nyro's voice (hotkey / barge-in / "stop")
        from host.overlay.island_client import NullIsland
        self._island = island or NullIsland()   # Dynamic Island status pill (no-op in tests)
        self._notify = notify or (lambda title, msg: None)
        self.cfg, self.data_dir, self.orch = cfg, data_dir, orchestrator
        self._rt = record_and_transcribe
        self._speak = speak or (lambda t: None)
        self._phone = send_phone or (lambda m: None)
        self._beep = beep or (lambda: None)
        self._busy = threading.Lock()
        self.last_reply: Reply | None = None

    # -- events from the orchestrator -> phone
    def on_event(self, ev: dict) -> None:
        # NB: orchestrator events carry their own "type"; keep it under "event" or it overwrites the envelope
        self._phone({"type": "event", "event": ev.get("type"), **{k: v for k, v in ev.items() if k != "type"}})
        if ev.get("type") == "tool" and ev.get("name") == "capture_foreground_window":
            self._send_preview()

    def _send_preview(self) -> None:
        try:
            files = sorted((self.data_dir / "captures").glob("capture_*.webp"))
            if files:
                self._phone({"type": "screenshot", "mime": "image/webp",
                             "b64": base64.b64encode(files[-1].read_bytes()).decode()})
        except Exception:
            pass

    # -- the pipeline
    def process_text(self, text: str, lang: str | None = None, ui: bool = False) -> Reply | None:
        """`ui=True` (voice/hotkey requests) also drives the Dynamic Island."""
        if not self._busy.acquire(blocking=False):
            return None  # already handling a request; ignore double triggers
        shown = False
        try:
            self._phone({"type": "heard", "text": text})
            if ui:
                self._island.state("thinking", text)
            started = time.monotonic()
            reply = self.orch.handle(text, lang)
            self.last_reply = reply
            if ui:
                self._island.state("error" if reply.source in ("error", "limit") else "done", reply.text)
                shown = True
            if reply.text and reply.source in ("llm", "error") and time.monotonic() - started > 15:
                try:  # long job (documents, desktop control): tell the user even if they looked away
                    self._notify("Nyro: done" if reply.source == "llm" else "Nyro: problem", reply.text[:240])
                except Exception:
                    pass
            self._phone({"type": "reply", "text": reply.text, "source": reply.source,
                         "persona": reply.persona, "budget": reply.budget})
            if reply.text and not reply.silent:
                self._speak(reply.text)
            return reply
        finally:
            if ui and not shown:   # the request crashed: never leave the island stuck on "thinking"
                self._island.state("error", "Something went wrong")
            self._busy.release()

    def listen_once(self, beep: bool = True, preroll: Any = None) -> Reply | None:
        """Wake word / hotkey / tray / voice barge-in: stop talking, island opens, record, transcribe, handle.
        `preroll` = mic audio from just before a barge-in was detected (so the first words are kept)."""
        if self._busy.locked() or self._rt is None:
            return None
        self._stop_speech()                  # never talk over (or record) our own voice
        self._island.state("listening")      # first thing: instant visual feedback
        if beep:
            self._beep()
        self._phone({"type": "listening"})
        try:
            text, lang = self._rt(preroll) if preroll is not None else self._rt()
        except Exception as e:
            print(f"[wake] recording failed: {e}", flush=True)
            self._island.state("error", "Microphone problem")
            self._phone({"type": "idle"})
            return None
        if not text:
            self._island.state("hide")
            self._phone({"type": "idle"})
            return None
        return self.process_text(text, lang, ui=True)

    # -- phone messages
    def on_phone_message(self, msg: dict, broker: ApprovalBroker | None = None) -> None:
        t = msg.get("type")
        if t == "command" and isinstance(msg.get("text"), str):
            threading.Thread(target=self.process_text, args=(msg["text"], msg.get("lang")), daemon=True).start()
        elif t == "approval_response" and broker is not None:
            try:
                broker.resolve(int(msg["id"]), bool(msg.get("approved")))
            except (KeyError, TypeError, ValueError):
                pass
        elif t == "audio" and isinstance(msg.get("b64"), str):
            threading.Thread(target=self._phone_audio, args=(msg,), daemon=True).start()
        elif t == "sync":
            s = self.orch.budget.status()
            self._phone({"type": "event", "event": "budget", "used": s.used, "cap": s.cap})

    MAX_AUDIO_BYTES = 300_000

    def _phone_audio(self, msg: dict) -> None:
        """Voice recorded on the phone: transcribe it here with Whisper and send the text back.
        The phone then sends it as a normal command (so it goes through the same router, budget and firewall)."""
        rid, lang = msg.get("rid"), msg.get("lang")
        if self._tb is None:
            self._phone({"type": "transcript", "rid": rid, "text": "", "error": "Voice is not available on the laptop"})
            return
        try:
            data = base64.b64decode(msg["b64"], validate=True)
            if len(data) > self.MAX_AUDIO_BYTES:
                raise ValueError("recording too long")
            text, detected = self._tb(data, lang if lang in ("en", "ur") else None)
            self._phone({"type": "transcript", "rid": rid, "text": text, "lang": detected})
        except Exception as e:
            print(f"[phone-voice] failed: {e}", flush=True)
            self._phone({"type": "transcript", "rid": rid, "text": "", "error": "Could not understand the recording"})


def relay_http_url(cfg: dict[str, Any]) -> str:
    """ws://host:port/ws/host -> http://host:port"""
    u = cfg["relay"]["url"].replace("wss://", "https://", 1).replace("ws://", "http://", 1)
    return u.split("/ws/")[0].rstrip("/")


def open_window(cfg: dict[str, Any], data_dir: Path) -> None:  # pragma: no cover - needs a desktop
    """Open the chat UI as a standalone app window (Edge/Chrome --app mode, own profile so login persists)."""
    candidates = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    exe = next((p for p in candidates if p.exists()), None)
    if exe is None:
        import webbrowser
        webbrowser.open(relay_http_url(cfg))
        return
    subprocess.Popen([str(exe), f"--app={relay_http_url(cfg)}", f"--user-data-dir={data_dir / 'window_profile'}",
                      "--window-size=460,780", "--no-first-run", "--no-default-browser-check"])


def main() -> int:  # pragma: no cover - needs Windows desktop + hardware
    cfg = load_config(os.environ.get("NYRO_CONFIG") or None)
    data_dir = Path(cfg["data_dir"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Path(cfg["budget"]["db_path"])
    budget = RequestBudget(db if db.is_absolute() else data_dir / db, cfg["budget"]["daily_request_cap"],
                           cfg["budget"]["warn_ratio"], cfg["budget"]["hourly_pace_warn"])

    from host.relay_client import RelayClient, token_from_env
    from host.voice import bargein, stt, tts, wakeword

    holder: dict[str, Any] = {}
    relay = RelayClient(cfg["relay"]["url"], token_from_env(cfg), lambda m: holder["app"].on_phone_message(m, holder["broker"]))
    orch = Orchestrator(cfg, budget, data_dir, lambda e: holder["app"].on_event(e))
    transcriber, recorder = stt.Transcriber(cfg["voice"]["stt_model"]), stt.Recorder()

    from host.overlay.island_client import IslandClient
    isl_cfg = {"enabled": True, "port": 8767, **(cfg.get("island") or {})}
    island = IslandClient(int(isl_cfg["port"]), bool(isl_cfg["enabled"]))

    def record_and_transcribe(preroll=None) -> tuple[str, str]:
        # mic level -> island waveform; (rms / 0.05) ** 0.6 maps normal speech to roughly 0.3-1.0
        audio = recorder.record(on_level=lambda rms: island.level(min(1.0, (rms / 0.05) ** 0.6)), preroll=preroll)
        island.state("thinking")             # transcribing
        return transcriber.transcribe(audio)

    def index_loop() -> None:
        """Keep the local file-search index fresh in the background (names fast, PDF/Word text a little at a time)."""
        from host import filemanager as FM
        time.sleep(20)                       # let Nyro finish starting first
        while True:
            pending = 0
            try:
                r = FM.refresh_index(cfg, budget_s=40.0)
                pending = int(r.get("text_pending", 0))
                if "skipped" not in r:
                    print(f"[index] {r.get('files', '?')} files indexed, {pending} still to read", flush=True)
            except Exception as e:
                print(f"[index] failed: {e}", flush=True)
            time.sleep(120 if pending else float((cfg.get("index") or {}).get("refresh_minutes", 20)) * 60)

    threading.Thread(target=index_loop, daemon=True).start()

    def on_barge_in(preroll) -> None:
        """The user started talking over Nyro: cut Nyro off and listen (keeping the first words from `preroll`)."""
        print("[barge-in] interrupted", flush=True)
        threading.Thread(target=holder["app"].listen_once, kwargs={"beep": False, "preroll": preroll}, daemon=True).start()

    def speak(text: str) -> None:
        t = tts.speak(text, cfg["voice"]["voices"])
        if cfg["voice"].get("barge_in", True):
            bargein.start_watch(t.is_alive, tts.playback_level, on_barge_in)

    app = NyroApp(cfg, data_dir, orch,
                  record_and_transcribe=record_and_transcribe,
                  transcribe_bytes=transcriber.transcribe_bytes,
                  island=island,
                  stop_speech=tts.stop,
                  speak=speak,
                  send_phone=relay.send,
                  beep=lambda: __import__("winsound").Beep(880, 120),
                  notify=lambda title, msg: holder["icon"].notify(msg, title) if holder.get("icon") else None)
    broker = ApprovalBroker(lambda p: relay.send({"type": "approval_request", "id": p.id, "action": p.action,
                                                  "summary": p.summary}),
                            cfg["approval"]["host"], cfg["approval"]["port"])
    holder.update(app=app, broker=broker)
    broker.start()
    overlay = subprocess.Popen([sys.executable, "-m", "host.overlay.screen_canvas"], cwd=str(ROOT))
    island_proc = None
    if isl_cfg["enabled"]:
        island_proc = subprocess.Popen([sys.executable, "-m", "host.overlay.island"], cwd=str(ROOT),
                                       env={**os.environ, "NYRO_ISLAND_PORT": str(isl_cfg["port"])})
    if token_from_env(cfg):
        relay.start()
    else:
        print(f"[nyro] {cfg['relay']['token_env']} not set: phone remote disabled")

    for eng in wakeword.build_engines(cfg, ROOT):
        eng.start(lambda: threading.Thread(target=app.listen_once, daemon=True).start())

    import pystray
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 64), (20, 20, 28))
    ImageDraw.Draw(img).ellipse((10, 10, 54, 54), outline=(255, 40, 60), width=6)

    def label(_=None):
        s = budget.status()
        return f"AI requests: {s.label()}" + (" (limit reached, local controls only)" if s.exhausted else "")

    show = lambda: open_window(cfg, data_dir)  # noqa: E731
    menu = pystray.Menu(
        pystray.MenuItem("Open Nyro", show, default=True),  # double-click the tray icon
        pystray.MenuItem(label, None, enabled=False),
        pystray.MenuItem("Listen now", lambda: threading.Thread(target=app.listen_once, daemon=True).start()),
        pystray.MenuItem("Stop mouse/keyboard control (Ctrl+Shift+Q)",
                         lambda: (data_dir / "control_grant.json").unlink(missing_ok=True)),
        pystray.MenuItem("Allow +100 requests today", lambda: budget.override_today(100)),
        pystray.MenuItem("Open data folder", lambda: os.startfile(str(data_dir))),  # type: ignore[attr-defined]
        pystray.MenuItem("Quit", lambda icon: icon.stop()),
    )
    icon = pystray.Icon("Nyro", img, "Nyro", menu)
    holder["icon"] = icon

    # Kill switch for mouse/keyboard control: Ctrl+Shift+Q revokes any active grant immediately.
    from host.desktopctl_impl import GRANT_FILE

    def stop_control() -> None:
        (data_dir / GRANT_FILE).unlink(missing_ok=True)
        try:
            __import__("winsound").Beep(440, 200)
        except Exception:
            pass
        print("[control] mouse/keyboard control revoked", flush=True)

    try:
        from pynput import keyboard as _kb
        _kill = _kb.GlobalHotKeys({"<ctrl>+<shift>+q": stop_control})
        _kill.daemon = True
        _kill.start()
    except Exception as e:
        print(f"[control] kill hotkey unavailable: {e}")
    print("[nyro] running. Hotkey:", cfg["wake_word"]["hotkey"], flush=True)
    if os.environ.get("NYRO_NO_WINDOW") != "1":
        show()
    try:
        icon.run()
    finally:
        broker.stop()
        relay.stop()
        overlay.terminate()
        if island_proc:
            island_proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
