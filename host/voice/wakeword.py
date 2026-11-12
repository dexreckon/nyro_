"""Wake engines: global hotkey (always works) + openwakeword ("Hey Nyro" once the model exists)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


def hotkey_to_pynput(s: str) -> str:
    parts = [x.strip().lower() for x in s.split("+")]
    return "+".join(f"<{x}>" if len(x) > 1 else x for x in parts)


class WakeGate:
    """Threshold + cooldown so one utterance fires once."""

    def __init__(self, threshold: float = 0.5, cooldown: float = 2.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown, self._clock = threshold, cooldown, clock
        self._last = -1e9

    def check(self, score: float) -> bool:
        now = self._clock()
        if score >= self.threshold and now - self._last >= self.cooldown:
            self._last = now
            return True
        return False


class HotkeyWake:
    name = "hotkey"

    def __init__(self, hotkey: str):
        self.hotkey = hotkey
        self._listener: Any = None

    def start(self, on_wake: Callable[[], None]) -> bool:
        try:
            from pynput import keyboard
            self._listener = keyboard.GlobalHotKeys({hotkey_to_pynput(self.hotkey): on_wake})
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as e:
            print(f"[wake] hotkey unavailable: {e}")
            return False

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()


class OpenWakeWordEngine:
    name = "openwakeword"
    FRAME = 1280  # 80 ms @ 16 kHz

    def __init__(self, model_path: str, threshold: float = 0.5):
        self.model_path, self.gate = model_path, WakeGate(threshold)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.paused = False

    def available(self) -> bool:
        return Path(self.model_path).is_file()

    def start(self, on_wake: Callable[[], None]) -> bool:
        if not self.available():
            print(f"[wake] model not found at {self.model_path}; 'Hey Nyro' disabled, hotkey still works")
            return False
        self._thread = threading.Thread(target=self._loop, args=(on_wake,), daemon=True)
        self._thread.start()
        return True

    def _loop(self, on_wake: Callable[[], None]) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            from openwakeword.model import Model
            model = Model(wakeword_models=[self.model_path], inference_framework="onnx")
            with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=self.FRAME) as st:
                while not self._stop.is_set():
                    data, _ = st.read(self.FRAME)
                    scores = model.predict(np.squeeze(data))
                    if self.paused:
                        continue
                    if scores and self.gate.check(max(scores.values())):
                        on_wake()
        except Exception as e:
            print(f"[wake] openwakeword stopped: {e}")

    def stop(self) -> None:
        self._stop.set()


def build_engines(cfg: dict[str, Any], base_dir: Path) -> list:
    w = cfg["wake_word"]
    engines: list = [HotkeyWake(w["hotkey"])]
    if w.get("engine") == "openwakeword":
        mp = Path(w["model_path"])
        engines.append(OpenWakeWordEngine(str(mp if mp.is_absolute() else base_dir / mp), w.get("threshold", 0.5)))
    return engines
