"""edge-tts voice output (English / Urdu). Network + audio libs are lazy."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading

from host.lang import detect_language

_DEFAULT_VOICES = {"en": "en-US-AriaNeural", "ur": "ur-PK-UzmaNeural"}


def pick_voice(text: str, voices: dict[str, str] | None = None, lang: str | None = None) -> str:
    voices = {**_DEFAULT_VOICES, **(voices or {})}
    return voices.get(lang or detect_language(text), voices["en"])


async def _synth(text: str, voice: str, path: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(path)


def _play(path: str) -> None:
    import time

    import miniaudio
    rate, ch = 48000, 2
    fmt = miniaudio.SampleFormat.SIGNED16
    # Decode once to exactly the device format (edge-tts gives 24 kHz mono): no pitch shift, no resampling mid-stream.
    samples = miniaudio.decode_file(path, output_format=fmt, nchannels=ch, sample_rate=rate).samples
    total = len(samples)
    done = threading.Event()

    def gen():
        pos = 0
        required = yield b""  # first send() tells us how many frames the device wants
        while pos < total:
            n = max(int(required), 1) * ch
            required = yield samples[pos:pos + n]  # device expects exactly the frames it asked for
            pos += n
        done.set()

    with miniaudio.PlaybackDevice(output_format=fmt, nchannels=ch, sample_rate=rate) as dev:
        g = gen()
        next(g)
        dev.start(g)
        done.wait(120)
        time.sleep(0.4)  # let the last buffer drain instead of cutting the tail


def speak_blocking(text: str, voices: dict[str, str] | None = None) -> bool:
    if not text.strip():
        return False
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        asyncio.run(_synth(text[:1500], pick_voice(text, voices), path))
        _play(path)
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
