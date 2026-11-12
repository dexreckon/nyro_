import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from host.app import NyroApp
from host.budget import RequestBudget
from host.config import load_config
from host.orchestrator import Orchestrator, Reply
from host.voice.stt import SAMPLE_RATE, endpoint, pick_language
from host.voice.tts import pick_voice
from host.voice.wakeword import WakeGate, build_engines, hotkey_to_pynput


def tone(seconds, amp=0.2, sr=SAMPLE_RATE):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def quiet(seconds, sr=SAMPLE_RATE):
    return (0.001 * np.random.default_rng(0).standard_normal(int(seconds * sr))).astype(np.float32)


class EndpointTests(unittest.TestCase):
    def test_stops_after_trailing_silence(self):
        audio = np.concatenate([quiet(0.4), tone(1.0), quiet(1.5)])
        end, spoke = endpoint(audio)
        self.assertTrue(spoke)
        self.assertLess(end, len(audio))
        self.assertGreater(end, int(1.4 * SAMPLE_RATE))          # kept all speech
        self.assertLess(end, int(2.5 * SAMPLE_RATE))             # cut soon after 0.9s of quiet

    def test_still_speaking_returns_full_length(self):
        audio = np.concatenate([quiet(0.4), tone(2.0)])
        end, spoke = endpoint(audio)
        self.assertEqual((end, spoke), (len(audio), True))

    def test_silence_only(self):
        end, spoke = endpoint(quiet(3.0))
        self.assertFalse(spoke)

    def test_short_click_ignored(self):
        audio = np.concatenate([quiet(0.5), tone(0.05), quiet(2.0)])
        self.assertFalse(endpoint(audio)[1])

    def test_empty(self):
        self.assertEqual(endpoint(np.zeros(0, dtype=np.float32)), (0, False))


class LangTests(unittest.TestCase):
    def test_pick_language(self):
        self.assertEqual(pick_language(None, "ur"), "ur")
        self.assertEqual(pick_language([("hi", 0.5), ("ur", 0.3), ("en", 0.1)], "hi"), "ur")  # Hindi/Urdu confusion
        self.assertEqual(pick_language([("fr", 0.9), ("en", 0.05), ("ur", 0.01)], "fr"), "en")
        self.assertEqual(pick_language(None, "de"), "en")

    def test_voices(self):
        self.assertEqual(pick_voice("hello there"), "en-US-AriaNeural")
        self.assertEqual(pick_voice("آواز کم ہو گئی"), "ur-PK-UzmaNeural")
        self.assertEqual(pick_voice("x", {"ur": "ur-PK-AsadNeural"}, lang="ur"), "ur-PK-AsadNeural")


class WakeTests(unittest.TestCase):
    def test_gate_threshold_and_cooldown(self):
        now = [0.0]
        g = WakeGate(0.5, 2.0, clock=lambda: now[0])
        self.assertFalse(g.check(0.3))
        self.assertTrue(g.check(0.9))
        now[0] = 1.0
        self.assertFalse(g.check(0.95))     # cooldown
        now[0] = 2.5
        self.assertTrue(g.check(0.95))

    def test_hotkey_format(self):
        self.assertEqual(hotkey_to_pynput("ctrl+alt+n"), "<ctrl>+<alt>+n")

    def test_engines_without_model_still_have_hotkey(self):
        cfg = load_config()
        engines = build_engines(cfg, Path("/nonexistent"))
        self.assertEqual([e.name for e in engines], ["hotkey", "openwakeword"])
        self.assertFalse(engines[1].available())
        self.assertFalse(engines[1].start(lambda: None))   # missing model: refuses politely


class AppPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = load_config()
        budget = RequestBudget(t / "b.db", 5, now=datetime.now)
        self.orch = Orchestrator(self.cfg, budget, t, local_runner=lambda i, c: "done",
                                 runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM expected")))
        self.phone, self.said, self.beeps = [], [], []
        self.app = NyroApp(self.cfg, t, self.orch,
                           record_and_transcribe=lambda: ("volume 20", "en"),
                           speak=self.said.append, send_phone=self.phone.append,
                           beep=lambda: self.beeps.append(1))

    def tearDown(self):
        self.tmp.cleanup()

    def test_listen_once_full_pipeline(self):
        r = self.app.listen_once()
        self.assertEqual((r.source, r.text), ("local", "done"))
        self.assertEqual(self.said, ["done"])
        self.assertEqual(self.beeps, [1])
        kinds = [m["type"] for m in self.phone]
        self.assertEqual(kinds[0], "listening")
        self.assertIn("heard", kinds)
        self.assertIn("reply", kinds)

    def test_empty_transcript_goes_idle(self):
        self.app._rt = lambda: ("", "en")
        self.assertIsNone(self.app.listen_once())
        self.assertEqual(self.phone[-1]["type"], "idle")

    def test_double_trigger_ignored_while_busy(self):
        self.app._busy.acquire()
        self.assertIsNone(self.app.listen_once())
        self.assertIsNone(self.app.process_text("mute"))
        self.app._busy.release()

    def test_phone_approval_routing(self):
        class B:
            got = None
            def resolve(self, i, a):
                B.got = (i, a)
        self.app.on_phone_message({"type": "approval_response", "id": "7", "approved": True}, B())
        self.assertEqual(B.got, (7, True))
        B.got = None
        self.app.on_phone_message({"type": "approval_response", "id": "nope"}, B())   # malformed: ignored
        self.assertIsNone(B.got)

    def test_phone_command_runs_pipeline(self):
        import time
        self.app.on_phone_message({"type": "command", "text": "mute"})
        for _ in range(100):
            if any(m["type"] == "reply" for m in self.phone):
                break
            time.sleep(0.02)
        self.assertTrue(any(m["type"] == "reply" for m in self.phone))

    def test_screenshot_preview_sent_on_capture_tool(self):
        cap = Path(self.tmp.name, "captures")
        cap.mkdir()
        (cap / "capture_1.webp").write_bytes(b"RIFFfake")
        self.app.on_event({"type": "tool", "name": "capture_foreground_window"})
        self.assertEqual(self.phone[-1]["type"], "screenshot")


if __name__ == "__main__":
    unittest.main()
