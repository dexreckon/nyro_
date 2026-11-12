import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

from host import sysop_impl as S
from host import webpilot_impl as W
from host.approval import ApprovalBroker, request_approval
from host.config import load_config
from host.firewall import PathJail, PathJailError
from host.mcp_servers import fileclerk_impl as F
from host.overlay.coords import scale_command, target_box_native
from host.overlay.scaler import downscale_to_webp
from host.screencoach_impl import Coach

CFG = load_config()


class FileClerkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "n.md").write_text("hello")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "c.cpp").write_text("int main(){}")
        (self.root / "bin.exe").write_bytes(b"MZ")
        self.jail = PathJail([self.root], CFG["firewall"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_list(self):
        self.assertEqual(F.list_allowed_files(self.jail), ["n.md", "sub/c.cpp"])

    def test_read_and_types(self):
        self.assertEqual(F.read_document(self.jail, "n.md"), "hello")
        with self.assertRaises(ValueError):
            F.read_document(self.jail, "bin.exe")
        with self.assertRaises(PathJailError):
            F.read_document(self.jail, "../x.md")

    def test_write(self):
        self.assertEqual(F.write_markdown(self.jail, "new/dir/a.md", "# hi"), "new/dir/a.md")
        self.assertEqual((self.root / "new/dir/a.md").read_text(), "# hi")
        with self.assertRaises(ValueError):
            F.write_markdown(self.jail, "a.txt", "x")
        with self.assertRaises(PathJailError):
            F.write_markdown(self.jail, "../evil.md", "x")
        with self.assertRaises(ValueError):
            F.write_markdown(self.jail, "big.md", "x" * 300_000)
        self.assertEqual([p.name for p in self.root.glob("**/*.tmp")], [])

    def test_trash_listing_hidden(self):
        F.soft_delete_file(self.jail, "n.md")
        self.assertNotIn(".nyro_trash", " ".join(F.list_allowed_files(self.jail)))


class WebPilotTests(unittest.TestCase):
    def test_urls(self):
        ok = ["http", "https"]
        self.assertEqual(W.check_url(" https://a.com/x ", ok), "https://a.com/x")
        for bad in ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "chrome://settings", "https://"]:
            with self.assertRaises(ValueError):
                W.check_url(bad, ok)

    def test_private_hosts(self):
        for h in ["localhost", "127.0.0.1", "192.168.1.1", "10.0.0.5", "169.254.169.254", "printer.local", "[::1]"]:
            self.assertTrue(W.is_private_host(h), h)
        self.assertFalse(W.is_private_host("example.com"))
        self.assertFalse(W.is_private_host("8.8.8.8"))

    def test_sensitive(self):
        self.assertTrue(W.is_sensitive_field("password", None))
        self.assertTrue(W.is_sensitive_field("text", "card_number"))
        self.assertFalse(W.is_sensitive_field("text", "search"))


class SysOpTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(S.clamp_level(150), 100)
        self.assertEqual(S.clamp_level(-3), 0)
        self.assertEqual(S.clamp_level(33.6), 34)
        for bad in ("50", None, True):
            with self.assertRaises(ValueError):
                S.clamp_level(bad)

    def test_telemetry(self):
        t = S.get_system_telemetry()
        self.assertIn("cpu_percent", t)
        self.assertIn("ram_percent", t)

    def test_window_action_validation(self):
        with self.assertRaises(ValueError):
            S.manage_window("notepad", "close")  # close deliberately not offered
        with self.assertRaises(ValueError):
            S.manage_window("", "maximize")

    @unittest.skipIf(S.IS_WINDOWS, "non-Windows behaviour")
    def test_unsupported_off_windows(self):
        with self.assertRaises(S.Unsupported):
            S.set_volume(10)


class CoordsTests(unittest.TestCase):
    def cap(self):
        return downscale_to_webp(Image.new("RGB", (1600, 900)), origin=(100, 50))

    def test_scale_each_kind(self):
        c = self.cap()  # factor 2.0
        self.assertEqual(scale_command(c, {"type": "cursor_point", "x": 10, "y": 20})["x"], 120)
        b = scale_command(c, {"type": "box_highlight", "x": 10, "y": 10, "w": 50, "h": 20})
        self.assertEqual((b["x"], b["y"], b["w"], b["h"]), (120, 70, 100, 40))
        a = scale_command(c, {"type": "arrow_connect", "x1": 0, "y1": 0, "x2": 800, "y2": 450})
        self.assertEqual((a["x1"], a["x2"], a["y2"]), (100, 1700, 950))
        ci = scale_command(c, {"type": "freehand_circle", "cx": 5, "cy": 5, "rx": 10, "ry": 4})
        self.assertEqual((ci["cx"], ci["rx"], ci["ry"]), (110, 20, 8))
        self.assertEqual(scale_command(c, {"type": "step_hud", "step": 1, "total": 2}), {"type": "step_hud", "step": 1, "total": 2})

    def test_clamped_overshoot(self):
        self.assertEqual(scale_command(self.cap(), {"type": "cursor_point", "x": 99999, "y": -9})["x"], 1700)

    def test_target_box(self):
        c = self.cap()
        self.assertEqual(target_box_native(c, [], [10, 10, 5, 5]), (120, 70, 10, 10))
        cmds = [{"type": "box_highlight", "x": 1, "y": 2, "w": 3, "h": 4}]
        self.assertEqual(target_box_native(c, cmds), (102, 54, 6, 8))
        self.assertEqual(target_box_native(c, [{"type": "cursor_point", "x": 100, "y": 100}], point_half=10),
                         (100 + 90 * 2, 50 + 90 * 2, 40, 40))
        self.assertIsNone(target_box_native(c, []))


class FakeWaiter:
    def __init__(self, results):
        self.results, self.boxes = list(results), []

    def wait(self, box, timeout):
        self.boxes.append(box)
        return self.results.pop(0)


class CoachTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drawn, self.said, self.cleared = [], [], []
        self.coach = Coach(
            self.tmp.name,
            capture_fn=lambda: downscale_to_webp(Image.new("RGB", (1600, 900)), origin=(0, 0)),
            draw_fn=lambda c: self.drawn.append(c) or True,
            clear_fn=lambda: self.cleared.append(1) or True,
            speak_fn=self.said.append, waiter=FakeWaiter([True, True]), click_timeout=1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_requires_capture(self):
        with self.assertRaises(RuntimeError):
            self.coach.render("[]")

    def test_capture_saves_webp_and_meta(self):
        info = self.coach.capture()
        self.assertEqual((info["width"], info["height"]), (800, 450))
        self.assertTrue(Path(info["path"]).exists())

    def test_capture_rotation(self):
        for _ in range(8):
            self.coach.capture()
            import time; time.sleep(0.002)
        self.assertLessEqual(len(list(Path(self.tmp.name, "captures").glob("capture_*.webp"))), 5)

    def test_single_draw_no_wait(self):
        self.coach.capture()
        out = self.coach.render(json.dumps([{"type": "cursor_point", "x": 100, "y": 50, "label": "x"}]), "hello")
        self.assertIn("Drew step 1/1", out)
        self.assertEqual(self.drawn[0][0]["x"], 200)
        self.assertEqual(self.said, ["hello"])

    def test_multistep_auto_advance_no_extra_capture(self):
        self.coach.capture()
        steps = {"steps": [
            {"title": "A", "speech": "one", "annotations": [{"type": "box_highlight", "x": 10, "y": 10, "w": 40, "h": 20}]},
            {"title": "B", "speech": "two", "annotations": [{"type": "cursor_point", "x": 100, "y": 100}]}]}
        out = self.coach.render(json.dumps(steps), auto_advance_on_click=True)
        self.assertEqual(out, "All 2 step(s) completed.")
        self.assertEqual(self.said, ["one", "two"])
        self.assertEqual(self.drawn[0][0]["type"], "step_hud")
        self.assertEqual(len(self.drawn), 2)
        self.assertEqual(len(list(self.coach.dir.glob("capture_*.webp"))), 1)  # no extra screenshot taken

    def test_timeout_stops(self):
        self.coach.capture()
        self.coach._waiter = FakeWaiter([False])
        out = self.coach.render(json.dumps([{"type": "cursor_point", "x": 1, "y": 1}]), auto_advance_on_click=True)
        self.assertIn("Stopped at step 1/1", out)

    def test_recapture_after(self):
        self.coach.capture()
        steps = {"steps": [
            {"annotations": [{"type": "box_highlight", "x": 1, "y": 1, "w": 9, "h": 9}], "recapture_after": True},
            {"annotations": [{"type": "cursor_point", "x": 5, "y": 5}]}]}
        out = self.coach.render(json.dumps(steps), auto_advance_on_click=True)
        self.assertIn("UI changed. New screenshot:", out)
        self.assertEqual(len(self.drawn), 1)  # step 2 not drawn: the model re-plans

    def test_bad_input_draws_nothing(self):
        self.coach.capture()
        for bad in ['{"steps": []}', '[{"type": "evil"}]', "not json"]:
            with self.assertRaises(Exception):
                self.coach.render(bad)
        self.assertEqual(self.drawn, [])

    def test_overlay_down(self):
        self.coach._draw = lambda c: False
        self.coach.capture()
        with self.assertRaises(RuntimeError):
            self.coach.render(json.dumps([{"type": "cursor_point", "x": 1, "y": 1}]))


class ApprovalTests(unittest.TestCase):
    def test_approve_reject_timeout(self):
        seen = []
        br = ApprovalBroker(seen.append, port=0)
        br.start()
        url = f"http://127.0.0.1:{br.port}/approve"
        try:
            for decision in (True, False):
                res = []
                before = len(seen)
                t = threading.Thread(target=lambda: res.append(request_approval("write_markdown", "w", {}, 5, url)))
                t.start()
                for _ in range(250):
                    if len(seen) > before:
                        break
                    time.sleep(0.02)
                p = seen[-1]
                self.assertTrue(br.resolve(p.id, decision))
                t.join(5)
                self.assertEqual(res, [decision])
            self.assertFalse(request_approval("x", "y", {}, 0.3, url))  # nobody answers -> denied
        finally:
            br.stop()

    def test_fail_closed_without_broker(self):
        self.assertFalse(request_approval("x", "y", {}, 1, "http://127.0.0.1:9/approve"))

    def test_non_loopback_refused(self):
        with self.assertRaises(ValueError):
            ApprovalBroker(lambda p: None, host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()


class InlineImageToolTests(unittest.TestCase):
    def test_capture_tool_returns_inline_webp(self):
        from mcp.server.fastmcp import Image as McpImage
        from host.mcp_servers import screencoach_mcp as sc
        with tempfile.TemporaryDirectory() as d:
            sc._coach = Coach(d, lambda: downscale_to_webp(Image.new("RGB", (1920, 1080))),
                              lambda c: True, lambda: True)
            try:
                out = sc.capture_foreground_window()
            finally:
                sc._coach = None
        self.assertIn("800x450", out[0])
        self.assertIsInstance(out[1], McpImage)
        self.assertEqual(out[1].data[:4], b"RIFF")  # WebP container
