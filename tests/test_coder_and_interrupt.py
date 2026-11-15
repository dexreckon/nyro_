import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from host import desktopctl_impl as D
from host import ide
from host.firewall import PathJail
from host.intent import Intent, route
from host.local_exec import execute
from host.mcp_servers import coder_impl as K
from host.voice import tts
from host.voice.bargein import BargeInDetector


class InterruptTests(unittest.TestCase):
    def test_stop_bumps_generation_so_old_speech_dies(self):
        g = tts._next_gen()
        self.assertTrue(tts._current(g))
        tts.stop()
        self.assertFalse(tts._current(g))
        g2 = tts._next_gen()                      # a new utterance also cuts off the previous one
        tts.stop()
        g3 = tts._next_gen()
        self.assertFalse(tts._current(g2))
        self.assertTrue(tts._current(g3))

    def test_stop_words_route_to_stop_command(self):
        for t in ("stop", "Stop.", "stop talking", "ok stop it", "ruk jao", "bas karo", "chup", "رکو", "रुको"):
            i = route(t)
            self.assertEqual((i.kind, i.action), ("local", "stop_speaking"), t)
        for t in ("stop the music and open youtube", "don't stop", "bus stop near me", "volume 20"):
            self.assertNotEqual(route(t).action, "stop_speaking", t)

    def test_stop_command_silences_and_revokes_control(self):
        with tempfile.TemporaryDirectory() as d:
            grant = Path(d) / D.GRANT_FILE
            grant.write_text(json.dumps({"expires": 9e12, "actions_left": 5}))
            g = tts._next_gen()
            out = execute(Intent("local", "sysop", "stop_speaking", {}, "en"),
                          {"data_dir": d, "firewall": {}})
            self.assertEqual(out, "Stopped.")
            self.assertFalse(grant.exists())
            self.assertFalse(tts._current(g))


class BargeInTests(unittest.TestCase):
    """Detector sees (mic loudness, Nyro's own playback loudness) per 50 ms block."""

    def run_blocks(self, det, blocks):
        return [det.feed(m, p) for m, p in blocks]

    def test_nyros_own_echo_never_triggers(self):
        det = BargeInDetector()
        # loud playback leaking into the mic at ~40% every block, with some wobble
        blocks = [(0.4 * p, p) for p in [0.2, 0.3, 0.25, 0.1, 0.35, 0.3] * 20]
        self.assertFalse(any(self.run_blocks(det, blocks)))

    def test_silence_never_triggers(self):
        det = BargeInDetector()
        self.assertFalse(any(self.run_blocks(det, [(0.004, 0.0)] * 100)))

    def test_user_talking_over_nyro_triggers(self):
        det = BargeInDetector()
        echo = [(0.08, 0.2)] * 14                      # warm-up: echo only (coupling 0.4)
        user = [(0.45, 0.2)] * 10                      # user is much louder than the echo
        fired = self.run_blocks(det, echo + user)
        self.assertFalse(any(fired[:14]))
        self.assertTrue(any(fired[14:]))

    def test_short_blip_does_not_trigger(self):
        det = BargeInDetector()
        blocks = [(0.08, 0.2)] * 14 + [(0.5, 0.2)] * 2 + [(0.08, 0.2)] * 20   # a cough/click, not speech
        self.assertFalse(any(self.run_blocks(det, blocks)))

    def test_user_speaking_while_nyro_pauses_triggers(self):
        det = BargeInDetector()
        blocks = [(0.08, 0.2)] * 14 + [(0.3, 0.0)] * 10     # Nyro silent between sentences, user speaks
        self.assertTrue(any(self.run_blocks(det, blocks)))


class CoderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jail = PathJail([self.tmp.name])

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_each_language_with_lf_endings(self):
        for name in ("hello.py", "sort.cpp", "app.js", "main.dart", "Main.java", "proj/utils/math.py"):
            rel = K.write_code_file(self.jail, name, "line1\nline2\n")
            self.assertEqual(rel, name)
        self.assertEqual((Path(self.tmp.name) / "main.dart").read_bytes(), b"line1\nline2\n")
        self.assertIn("proj/utils/math.py", K.list_code_files(self.jail))

    def test_dangerous_types_and_escapes_are_refused(self):
        for bad in ("run.bat", "x.ps1", "x.exe", "evil.vbs", "noext", "image.png"):
            with self.assertRaises(ValueError, msg=bad):
                K.write_code_file(self.jail, bad, "x")
        with self.assertRaises(Exception):
            K.write_code_file(self.jail, "../outside.py", "x")
        with self.assertRaises(Exception):
            K.write_code_file(self.jail, "C:/Windows/system32/x.py", "x")
        with self.assertRaises(ValueError):
            K.write_code_file(self.jail, "big.py", "x" * 300_000)

    def test_exists_flag_drives_overwrite_approval(self):
        self.assertFalse(K.exists(self.jail, "a.py"))
        K.write_code_file(self.jail, "a.py", "1")
        self.assertTrue(K.exists(self.jail, "a.py"))
        self.assertEqual(K.read_code_file(self.jail, "a.py"), "1")

    def test_open_in_ide_uses_named_ide_and_refuses_unknown(self):
        K.write_code_file(self.jail, "a.cpp", "int main(){}")
        with mock.patch.object(ide, "open_in_ide", return_value="Android Studio") as m:
            out = K.open_in_ide(self.jail, "a.cpp", "androidstudio")
        self.assertTrue(out.startswith("Android Studio:"))
        self.assertEqual(m.call_args.args[0], "androidstudio")
        with self.assertRaises(ValueError):
            ide.find_ide("notepad-evil")

    def test_missing_ide_gives_helpful_error(self):
        with mock.patch.object(ide, "find_ide", return_value=None):
            with self.assertRaises(LookupError):
                ide.open_in_ide("clion", "x.cpp")


class CodeRoutingTests(unittest.TestCase):
    def test_code_requests_go_to_coder(self):
        for t in ("make a python file in vs code", "write a C++ program in CLion", "create hello.dart",
                  "make me a javascript file", "create main.cpp in android studio",
                  "write hello world in python", "make a dot py file that prints hello",
                  "write a c# program", "create a Java class for a stack in IntelliJ"):
            i = route(t)
            self.assertEqual((i.kind, i.persona), ("llm", "coder"), t)

    def test_typing_in_an_ide_is_still_mouse_keyboard_work(self):
        self.assertEqual(route("open vs code and type hello").persona, "desktopctl")
        self.assertEqual(route("click the run button in vs code").persona, "desktopctl")

    def test_plain_open_ide_is_local(self):
        for t, target in (("open vs code", "vscode"), ("open android studio", "androidstudio"),
                          ("launch pycharm", "pycharm")):
            i = route(t)
            self.assertEqual((i.kind, i.action, i.params["target"]), ("local", "open_target", target), t)

    def test_assignment_docs_still_go_to_fileclerk(self):
        self.assertEqual(route("make a solution word file for the assignment pdf").persona, "fileclerk")


if __name__ == "__main__":
    unittest.main()
