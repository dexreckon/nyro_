import json
import tempfile
import unittest
from pathlib import Path

from host.desktopctl_impl import ControlDenied, DesktopControl, parse_combo
from host.firewall import PathJail
from host.intent import route
from host.mcp_servers import fileclerk_impl as F
from host.orchestrator import Orchestrator

def make_pdf(text: str) -> bytes:
    """Tiny valid one-page PDF (with a correct xref table) containing `text`."""
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return out + b"trailer<</Root 1 0 R/Size %d>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


MINIMAL_PDF = make_pdf("Question 1: add 2 and 3")


class FileClerkDocTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jail = PathJail([self.root])

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_pdf_and_list_newest_first_with_query(self):
        (self.root / "Assignment 1.pdf").write_bytes(MINIMAL_PDF)
        (self.root / "notes.md").write_text("hi")
        self.assertEqual(F.list_allowed_files(self.jail, query="assign"), ["Assignment 1.pdf"])
        self.assertIn("Question 1", F.read_document(self.jail, "Assignment 1.pdf"))

    def test_write_docx_roundtrip(self):
        rel = F.write_docx(self.jail, "Nyro Solutions/Sol.docx",
                           "# Answer\n1. Add: **5**\n- bullet\n```\nprint(2+3)\n```", "Assignment 1")
        self.assertEqual(rel, "Nyro Solutions/Sol.docx")
        text = F.read_document(self.jail, rel)
        for want in ("Assignment 1", "Answer", "Add: 5", "bullet", "print(2+3)"):
            self.assertIn(want, text)

    def test_write_docx_rejects_wrong_extension_and_escape(self):
        with self.assertRaises(ValueError):
            F.write_docx(self.jail, "x.exe", "a")
        with self.assertRaises(Exception):
            F.write_docx(self.jail, "../evil.docx", "a")

    def test_open_path_refuses_executables(self):
        (self.root / "run.exe").write_bytes(b"x")
        with self.assertRaises(ValueError):
            F.open_path(self.jail, "run.exe")


class FakeBackend:
    def __init__(self):
        self.pos, self.title, self.log = (500, 500), "Document1 - Word", []

    def position(self): return self.pos
    def foreground_title(self): return self.title
    def click(self, x, y, b, d): self.log.append(("click", x, y, b, d))
    def scroll(self, dx, dy): self.log.append(("scroll", dy))
    def type_text(self, t): self.log.append(("type", t))
    def press(self, parts): self.log.append(("press", parts))


class DesktopControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.b = FakeBackend()
        self.now = [1000.0]
        self.c = DesktopControl(self.tmp.name, self.b, now=lambda: self.now[0])
        self.c.save_capture(0, 0, 2.0, 2000, 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nothing_works_without_grant(self):
        with self.assertRaises(ControlDenied):
            self.c.type_text("hi")
        self.assertEqual(self.b.log, [])

    def test_grant_then_actions_and_coordinate_scaling(self):
        self.c.grant("write answer")
        self.c.click(100, 50)
        self.c.type_text("hello")
        self.c.press_keys("ctrl+s")
        self.assertEqual(self.b.log[0], ("click", 200, 100, "left", False))
        self.assertEqual(self.b.log[2], ("press", ["ctrl", "s"]))

    def test_expiry_and_action_limit_and_revoke(self):
        self.c.grant("x", minutes=1, max_actions=1)
        self.c.type_text("a")
        with self.assertRaises(ControlDenied):
            self.c.type_text("b")            # action limit
        self.c.grant("x", minutes=1)
        self.now[0] += 61
        with self.assertRaises(ControlDenied):
            self.c.type_text("c")            # expired
        self.c.grant("x")
        self.assertTrue(self.c.revoke())
        with self.assertRaises(ControlDenied):
            self.c.type_text("d")            # revoked (Ctrl+Shift+Q)

    def test_corner_failsafe_revokes(self):
        self.c.grant("x")
        self.b.pos = (0, 0)
        with self.assertRaises(ControlDenied):
            self.c.click(10, 10)
        self.b.pos = (500, 500)
        with self.assertRaises(ControlDenied):
            self.c.click(10, 10)             # grant is gone, not just paused

    def test_sensitive_windows_and_secrets_blocked(self):
        self.c.grant("x")
        for title in ("Windows PowerShell", "Task Manager", "Sign in to your account", "Settings"):
            self.b.title = title
            with self.assertRaises(ControlDenied):
                self.c.type_text("hello")
        self.b.title = "Document1 - Word"
        with self.assertRaises(ControlDenied):
            self.c.type_text("4111 1111 1111 1111")

    def test_blocked_key_combos(self):
        for combo in ("win+r", "alt+f4", "ctrl+shift+esc", "ctrl+alt+delete", "ctrl+x+y+z+w"):
            with self.assertRaises(ControlDenied):
                parse_combo(combo)
        self.assertEqual(parse_combo("Ctrl+Shift+S"), ["ctrl", "shift", "s"])
        self.assertEqual(parse_combo("enter"), ["enter"])

    def test_clicks_stay_on_screen_and_out_of_corner(self):
        self.assertEqual(self.c.to_native(-50, -50)[0] > 3, True)
        self.assertLessEqual(self.c.to_native(99999, 99999)[0], 1999)


class RoutingAndOverrideTests(unittest.TestCase):
    def test_assignment_goes_to_fileclerk(self):
        for t in ("open the downloads folder and there is an assignment pdf make me a solution word file",
                  "help me with my assignment pdf"):
            self.assertEqual(route(t).persona, "fileclerk", t)

    def test_mouse_keyboard_goes_to_desktopctl(self):
        self.assertEqual(route("use my mouse and keyboard to fill this in").persona, "desktopctl")

    def test_explain_this_still_screencoach(self):
        self.assertEqual(route("explain this").persona, "screencoach")

    def test_persona_override_applies(self):
        cfg = {"model": "haiku", "effort": "low", "claude": {"timeout_seconds": 120, "max_turns": 8},
               "session": {"reset_every_turns": 5}, "approval": {}, "overlay": {},
               "persona_overrides": {"fileclerk": {"model": "sonnet", "effort": "medium",
                                                   "timeout_seconds": 900, "max_turns": 24}}}
        o = Orchestrator(cfg, budget=None, data_dir=Path(tempfile.gettempdir()))
        eff, timeout = o._effective_cfg("fileclerk")
        self.assertEqual((eff["model"], eff["effort"], eff["claude"]["max_turns"], timeout), ("sonnet", "medium", 24, 900.0))
        eff, timeout = o._effective_cfg("sysop")
        self.assertEqual((eff["model"], eff["claude"]["max_turns"], timeout), ("haiku", 8, 120.0))


if __name__ == "__main__":
    unittest.main()
