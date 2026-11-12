import os
import tempfile
import unittest
from pathlib import Path

from host.config import load_config
from host.firewall import (PathJail, PathJailError, RedBlockError, Risk, classify_action,
                           enforce, is_protected_path, soft_delete)

CFG = load_config()["firewall"]


class JailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "notes"
        self.root.mkdir()
        self.outside = base / "secret.txt"
        self.outside.write_text("secret")
        (self.root / "a.md").write_text("hi")
        self.jail = PathJail([self.root], CFG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_inside_ok(self):
        self.assertEqual(self.jail.resolve("a.md"), self.root / "a.md")

    def test_dotdot_escape(self):
        with self.assertRaises(PathJailError):
            self.jail.resolve("../secret.txt")

    def test_absolute_outside(self):
        with self.assertRaises(PathJailError):
            self.jail.resolve(str(self.outside))

    def test_absolute_inside_ok(self):
        self.assertEqual(self.jail.resolve(str(self.root / "a.md")), self.root / "a.md")

    def test_sibling_prefix_trick(self):
        sib = self.root.parent / "notes_evil"
        sib.mkdir()
        (sib / "x.md").write_text("x")
        with self.assertRaises(PathJailError):
            self.jail.resolve(str(sib / "x.md"))

    @unittest.skipIf(os.name == "nt", "symlink perms on Windows")
    def test_symlink_escape(self):
        (self.root / "link").symlink_to(self.outside)
        with self.assertRaises(PathJailError):
            self.jail.resolve("link")

    def test_null_byte(self):
        with self.assertRaises(PathJailError):
            self.jail.resolve("a.md\x00.txt")

    def test_no_roots(self):
        with self.assertRaises(PathJailError):
            PathJail([], CFG)

    def test_windows_absolute_rejected(self):
        with self.assertRaises(PathJailError):
            self.jail.resolve("C:\\Windows\\System32\\cmd.exe")


class SoftDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "a.md").write_text("hi")
        self.jail = PathJail([self.root], CFG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_moves_to_trash(self):
        dest = soft_delete(self.jail, "a.md")
        self.assertFalse((self.root / "a.md").exists())
        self.assertTrue(dest.exists())
        self.assertEqual(dest.parent.name, ".nyro_trash")
        self.assertEqual(dest.read_text(), "hi")

    def test_missing(self):
        with self.assertRaises(FileNotFoundError):
            soft_delete(self.jail, "nope.md")

    def test_cannot_delete_trash(self):
        dest = soft_delete(self.jail, "a.md")
        with self.assertRaises(PathJailError):
            soft_delete(self.jail, str(dest.relative_to(self.root)))

    def test_escape_blocked(self):
        with self.assertRaises(PathJailError):
            soft_delete(self.jail, "../x")

    def test_directory_refused(self):
        (self.root / "d").mkdir()
        with self.assertRaises(PathJailError):
            soft_delete(self.jail, "d")


class RiskTests(unittest.TestCase):
    def test_protected_paths(self):
        for p in ["C:\\Windows\\System32\\drivers", "c:/windows/system32", "C:\\Program Files\\x",
                  "D:\\foo\\system32\\bar", "/etc/passwd"]:
            self.assertTrue(is_protected_path(p, CFG), p)
        self.assertFalse(is_protected_path("C:\\Users\\me\\notes\\a.md", CFG))

    def test_green(self):
        for a in ["render_screen_annotations", "capture_foreground_window", "set_volume",
                  "set_brightness", "read_document", "clear_annotations"]:
            self.assertIs(classify_action(a, {}, CFG), Risk.GREEN, a)

    def test_yellow(self):
        self.assertIs(classify_action("write_markdown", {"rel_path": "a.md"}, CFG), Risk.YELLOW)
        self.assertIs(classify_action("soft_delete_file", {"rel_path": "a.md"}, CFG), Risk.YELLOW)
        self.assertIs(classify_action("click_element", {"selector": "button#Submit"}, CFG), Risk.YELLOW)
        self.assertIs(classify_action("click_element", {"selector": "Pay now"}, CFG), Risk.YELLOW)
        self.assertIs(classify_action("some_new_tool", {}, CFG), Risk.YELLOW)

    def test_browser_click_green(self):
        self.assertIs(classify_action("click_element", {"selector": "a.next-page"}, CFG), Risk.GREEN)

    def test_red(self):
        self.assertIs(classify_action("bash", {"cmd": "ls"}, CFG), Risk.RED)
        self.assertIs(classify_action("run_command", {"cmd": "format c:"}, CFG), Risk.RED)
        self.assertIs(classify_action("read_document", {"rel_path": "C:\\Windows\\System32\\config\\SAM"}, CFG), Risk.RED)
        self.assertIs(classify_action("manage_window", {"app_name": "reg add HKLM\\Software"}, CFG), Risk.RED)
        self.assertIs(classify_action("kill_process", {"name": "lsass.exe"}, CFG), Risk.RED)
        self.assertIs(classify_action("kill_process", {"name": "notepad.exe"}, CFG), Risk.YELLOW)

    def test_enforce_raises(self):
        with self.assertRaises(RedBlockError):
            enforce("bash", {"cmd": "whoami"}, CFG)
        self.assertIs(enforce("set_volume", {"level": 30}, CFG), Risk.GREEN)


if __name__ == "__main__":
    unittest.main()
