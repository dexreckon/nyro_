"""Nyro tray app: wake word / hotkey / phone -> STT -> orchestrator -> voice + overlay + phone."""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
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
                 beep: Callable[[], None] | None = None):
        self.cfg, self.data_dir, self.orch = cfg, data_dir, orchestrator
        self._rt = record_and_transcribe
        self._speak = speak or (lambda t: None)
        self._phone = send_phone or (lambda m: None)
        self._beep = beep or (lambda: None)
        self._busy = threading.Lock()
        self.last_reply: Reply | None = None

    # -- events from the orchestrator -> phone
    def on_event(self, ev: dict) -> None:
        self._phone({"type": "event", **ev})
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
    def process_text(self, text: str, lang: str | None = None) -> Reply | None:
        if not self._busy.acquire(blocking=False):
            return None  # already handling a request; ignore double triggers
        try:
            self._phone({"type": "heard", "text": text})
            reply = self.orch.handle(text, lang)
            self.last_reply = reply
            self._phone({"type": "reply", "text": reply.text, "source": reply.source,
                         "persona": reply.persona, "budget": reply.budget})
            if reply.text:
                self._speak(reply.text)
            return reply
        finally:
            self._busy.release()

    def listen_once(self) -> Reply | None:
        """Wake word / hotkey / tray: beep, record, transcribe, handle."""
        if self._busy.locked() or self._rt is None:
            return None
        self._beep()
        self._phone({"type": "listening"})
        text, lang = self._rt()
        if not text:
            self._phone({"type": "idle"})
            return None
        return self.process_text(text, lang)

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
        elif t == "sync":
            s = self.orch.budget.status()
            self._phone({"type": "event", "event": "budget", "used": s.used, "cap": s.cap})


def main() -> int:  # pragma: no cover - needs Windows desktop + hardware
    cfg = load_config(os.environ.get("NYRO_CONFIG") or None)
    data_dir = Path(cfg["data_dir"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Path(cfg["budget"]["db_path"])
    budget = RequestBudget(db if db.is_absolute() else data_dir / db, cfg["budget"]["daily_request_cap"],
                           cfg["budget"]["warn_ratio"], cfg["budget"]["hourly_pace_warn"])

    from host.relay_client import RelayClient, token_from_env
    from host.voice import stt, tts, wakeword

    holder: dict[str, Any] = {}
    relay = RelayClient(cfg["relay"]["url"], token_from_env(cfg), lambda m: holder["app"].on_phone_message(m, holder["broker"]))
    orch = Orchestrator(cfg, budget, data_dir, lambda e: holder["app"].on_event(e))
    transcriber, recorder = stt.Transcriber(cfg["voice"]["stt_model"]), stt.Recorder()
    app = NyroApp(cfg, data_dir, orch,
                  record_and_transcribe=lambda: transcriber.transcribe(recorder.record()),
                  speak=lambda t: tts.speak(t, cfg["voice"]["voices"]),
                  send_phone=relay.send,
                  beep=lambda: __import__("winsound").Beep(880, 120))
    broker = ApprovalBroker(lambda p: relay.send({"type": "approval_request", "id": p.id, "action": p.action,
                                                  "summary": p.summary}),
                            cfg["approval"]["host"], cfg["approval"]["port"])
    holder.update(app=app, broker=broker)
    broker.start()
    overlay = subprocess.Popen([sys.executable, "-m", "host.overlay.screen_canvas"], cwd=str(ROOT))
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

    menu = pystray.Menu(
        pystray.MenuItem(label, None, enabled=False),
        pystray.MenuItem("Listen now", lambda: threading.Thread(target=app.listen_once, daemon=True).start()),
        pystray.MenuItem("Allow +100 requests today", lambda: budget.override_today(100)),
        pystray.MenuItem("Open data folder", lambda: os.startfile(str(data_dir))),  # type: ignore[attr-defined]
        pystray.MenuItem("Quit", lambda icon: icon.stop()),
    )
    icon = pystray.Icon("Nyro", img, "Nyro", menu)
    try:
        icon.run()
    finally:
        broker.stop()
        relay.stop()
        overlay.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
