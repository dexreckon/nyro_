import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from host.overlay.island_client import IslandClient, NullIsland  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from host.overlay.island import Island
    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


class FakeIsland:
    def __init__(self):
        self.calls = []

    def state(self, s, t=""): self.calls.append((s, t))
    def level(self, v): pass


class ClientTests(unittest.TestCase):
    def test_client_never_raises(self):
        c = IslandClient(port=9)      # nothing listens there
        c.state("listening"); c.level(5.0); c.level(-1)
        NullIsland().state("done", "x")


@unittest.skipUnless(HAVE_QT, "PyQt6 not available")
class IslandWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_frames(self, isl, n=240):
        for _ in range(n):
            isl.step(1 / 60)

    def test_expands_then_collapses_and_hides(self):
        isl = Island(bind=False)
        isl.set_state("listening")
        self.run_frames(isl)
        self.assertAlmostEqual(isl.w, 320.0, delta=4)
        isl.set_level(0.9)
        self.run_frames(isl, 30)
        self.assertGreater(max(isl.hist), 0.3)       # waveform follows the mic level
        isl.set_state("hide")
        self.run_frames(isl, 300)
        self.assertEqual(isl.state, "hidden")
        self.assertFalse(isl.isVisible())

    def test_done_auto_hides_after_hold(self):
        isl = Island(bind=False)
        isl.set_state("done", "Opening youtube in chrome.")
        isl.deadline = 0.0                            # already past
        self.run_frames(isl, 400)
        self.assertEqual(isl.state, "hidden")

    def test_wide_text_is_clamped(self):
        isl = Island(bind=False)
        isl.set_state("done", "x " * 300)
        self.assertLessEqual(isl.tw, 500.0)


class NyroAppIslandTests(unittest.TestCase):
    def test_voice_request_drives_island_states(self):
        from pathlib import Path
        from host.app import NyroApp
        from host.orchestrator import Reply

        class Orch:
            def handle(self, text, lang=None): return Reply("Done.", "local")

        isl = FakeIsland()
        app = NyroApp({}, Path("."), Orch(), record_and_transcribe=lambda: ("volume 30", "en"), island=isl)
        app.listen_once()
        self.assertEqual([s for s, _ in isl.calls], ["listening", "thinking", "done"])

    def test_silence_hides_island(self):
        from pathlib import Path
        from host.app import NyroApp
        isl = FakeIsland()
        NyroApp({}, Path("."), object(), record_and_transcribe=lambda: ("", "en"), island=isl).listen_once()
        self.assertEqual([s for s, _ in isl.calls], ["listening", "hide"])

    def test_typed_request_does_not_open_island(self):
        from pathlib import Path
        from host.app import NyroApp
        from host.orchestrator import Reply

        class Orch:
            def handle(self, text, lang=None): return Reply("ok", "local")

        isl = FakeIsland()
        NyroApp({}, Path("."), Orch(), island=isl).process_text("volume 30")
        self.assertEqual(isl.calls, [])


if __name__ == "__main__":
    unittest.main()
