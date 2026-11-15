"""Voice barge-in: notice the user talking over Nyro and cut Nyro off.

Laptop speakers leak into the microphone, so a plain "mic is loud" test would make Nyro interrupt itself.
Instead the detector learns, during the first half second of each reply, how loud Nyro's own voice appears in
the mic (the "coupling"), then only fires when the mic is clearly louder than that echo for ~300 ms."""
from __future__ import annotations

import statistics
import threading
from collections import deque
from typing import Callable

SR = 16000
BLOCK = SR // 20          # 50 ms


class BargeInDetector:
    def __init__(self, arm_blocks: int = 10, factor: float = 2.6, floor: float = 0.025, need_blocks: int = 6):
        self.arm, self.factor, self.floor, self.need = arm_blocks, factor, floor, need_blocks
        self.n = 0                       # blocks of Nyro actually speaking that we have listened to
        self.ratios: list[float] = []
        self.coupling: float | None = None
        self.hits = 0
        self._recent_play: deque[float] = deque(maxlen=4)   # speaker -> mic delay: compare against the recent peak

    def feed(self, mic_rms: float, play_rms: float) -> bool:
        """One 50 ms block: loudness at the microphone and of what Nyro is currently playing. True = user is talking."""
        self._recent_play.append(play_rms)
        play = max(self._recent_play)
        if self.n < self.arm:            # warm-up: learn how loud Nyro's own voice is at the mic
            if play > 0.01:
                self.n += 1
                self.ratios.append(mic_rms / play)
            else:
                self.hits = 0            # Nyro has not started speaking yet (still synthesising): nothing to learn
            return False
        if self.coupling is None:
            self.coupling = min(max(statistics.median(self.ratios), 0.02), 3.0) if self.ratios else 0.3
        threshold = max(self.floor, self.factor * self.coupling * play + self.floor)
        self.hits = self.hits + 1 if mic_rms > threshold else max(0, self.hits - 2)
        return self.hits >= self.need


def watch(is_alive: Callable[[], bool], level_fn: Callable[[], float], on_trigger: Callable[[object], None],
          preroll_s: float = 0.55) -> None:
    """While Nyro is speaking (is_alive), listen on the mic. On barge-in call on_trigger(preroll_audio) where
    preroll_audio is the last ~0.55 s of mic audio (the start of what the user said)."""
    import numpy as np
    det = BargeInDetector()
    ring: deque = deque(maxlen=int(preroll_s / 0.05) + 1)
    triggered = None
    try:
        import sounddevice as sd
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK) as stream:
            while is_alive():
                data, _ = stream.read(BLOCK)
                x = data[:, 0].copy()
                ring.append(x)
                if det.feed(float(np.sqrt(np.mean(x * x))), level_fn()):
                    triggered = np.concatenate(list(ring))
                    break
    except Exception as e:                # no mic / device busy: barge-in is just unavailable, never a crash
        print(f"[barge-in] unavailable: {e}", flush=True)
        return
    if triggered is not None:             # stream is closed now, so the recorder can open the microphone
        on_trigger(triggered)


def start_watch(is_alive, level_fn, on_trigger) -> threading.Thread:
    t = threading.Thread(target=watch, args=(is_alive, level_fn, on_trigger), daemon=True)
    t.start()
    return t
