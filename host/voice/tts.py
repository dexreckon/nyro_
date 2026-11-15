"""edge-tts voice output (English / Urdu). Network + audio libs are lazy.

Speech can be interrupted at any time: stop() (hotkey, "stop" command, voice barge-in) silences the current
utterance within ~50 ms, and starting a new utterance always cuts off the previous one."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time

from host.lang import detect_language

_DEFAULT_VOICES = {"en": "en-US-AriaNeural", "ur": "ur-PK-UzmaNeural"}

_lock = threading.Lock()
_gen = 0                  # bumped by stop() and by every new utterance; an utterance only plays while its gen is current
_speaking = False
_play_rms = 0.0           # loudness (0..1) of what is being played right now; used by the barge-in echo model


def pick_voice(text: str, voices: dict[str, str] | None = None, lang: str | None = None) -> str:
    voices = {**_DEFAULT_VOICES, **(voices or {})}
    return voices.get(lang or detect_language(text), voices["en"])


def stop() -> None:
    """Silence whatever Nyro is saying right now (and cancel anything still being synthesised)."""
    global _gen
    with _lock:
        _gen += 1


def is_speaking() -> bool:
    return _speaking


def playback_level() -> float:
    """Loudness 0..1 of the audio currently coming out of the speakers (0 when silent)."""
    return _play_rms if _speaking else 0.0


def _next_gen() -> int:
    global _gen
    with _lock:
        _gen += 1
        return _gen


def _current(gen: int) -> bool:
    return gen == _gen


async def _synth(text: str, voice: str, path: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(path)


def _play(path: str, gen: int) -> None:
    global _speaking, _play_rms
    import miniaudio
    import numpy as np
    rate, ch = 48000, 2
    fmt = miniaudio.SampleFormat.SIGNED16
    # Decode once to exactly the device format (edge-tts gives 24 kHz mono): no pitch shift, no resampling mid-stream.
    samples = miniaudio.decode_file(path, output_format=fmt, nchannels=ch, sample_rate=rate).samples
    total = len(samples)
    done = threading.Event()

    def gen_audio():
        pos = 0
        required = yield b""  # first send() tells us how many frames the device wants
        while pos < total and _current(gen):
            n = max(int(required), 1) * ch
            chunk = samples[pos:pos + n]
            if len(chunk):
                _play_rms_update(np.frombuffer(chunk, dtype=np.int16))
            required = yield chunk  # device expects exactly the frames it asked for
            pos += n
        done.set()

    def _play_rms_update(arr) -> None:
        global _play_rms
        rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2))) / 32768.0
        _play_rms = max(rms, _play_rms * 0.85)   # fast attack, short release

    _speaking = True
    try:
        with miniaudio.PlaybackDevice(output_format=fmt, nchannels=ch, sample_rate=rate) as dev:
            g = gen_audio()
            next(g)
            dev.start(g)
            deadline = time.monotonic() + 120
            while not done.wait(0.03):
                if not _current(gen) or time.monotonic() > deadline:   # interrupted: closing the device cuts the sound
                    break
            else:
                time.sleep(0.4)  # finished normally: let the last buffer drain instead of cutting the tail
    finally:
        _speaking = False
        _play_rms = 0.0


def speak_blocking(text: str, voices: dict[str, str] | None = None) -> bool:
    if not text.strip():
        return False
    gen = _next_gen()                      # a new utterance cuts off any earlier one
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        asyncio.run(_synth(text[:1500], pick_voice(text, voices), path))
        if not _current(gen):              # stopped (or superseded) while it was being synthesised
            return False
        _play(path, gen)
        return True
    except Exception as e:  # offline / no audio device: stay silent, never crash
        print(f"[tts] unavailable: {e}")
        return False
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def speak(text: str, voices: dict[str, str] | None = None) -> threading.Thread:
    t = threading.Thread(target=speak_blocking, args=(text, voices), daemon=True)
    t.start()
    return t
