import json
import os
import unittest
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage, QColor
    from PyQt6.QtGui import QPainter
    from PyQt6.QtWidgets import QApplication
    from host.overlay.screen_canvas import ScreenCanvas
    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False

from host.overlay.bridge import OverlayBridge
from host.overlay.commands import validate_batch


def _post(port, path, body, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=3)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.got = []
        self.br = OverlayBridge(self.got.append, port=0)
        self.br.start()

    def tearDown(self):
        self.br.stop()

    def test_draw_ok(self):
        self.assertEqual(_post(self.br.port, "/draw", {"type": "cursor_point", "x": 1, "y": 2}), 200)
        self.assertEqual(self.got[0][0].kind, "cursor_point")

    def test_clear(self):
        self.assertEqual(_post(self.br.port, "/clear", {}), 200)
        self.assertEqual(self.got[0][0].kind, "clear")

    def test_bad_command(self):
        self.assertEqual(_post(self.br.port, "/draw", {"type": "evil"}), 400)
        self.assertEqual(self.got, [])

    def test_browser_origin_blocked(self):
        code = _post(self.br.port, "/draw", {"type": "clear"}, {"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(self.got, [])

    def test_wrong_content_type(self):
        self.assertEqual(_post(self.br.port, "/draw", {"type": "clear"}, {"Content-Type": "text/plain"}), 415)

    def test_non_loopback_refused(self):
        with self.assertRaises(ValueError):
            OverlayBridge(lambda c: None, host="0.0.0.0", port=0)


@unittest.skipUnless(HAVE_QT, "PyQt6 not available")
class CanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self):
        c = ScreenCanvas()
        c.resize(1000, 700)
        return c

    def test_flags_are_click_through(self):
        c = self.make()
        f = c.windowFlags()
        for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint,
                     Qt.WindowType.WindowTransparentForInput, Qt.WindowType.Tool):
            self.assertTrue(f & flag, flag)
        self.assertTrue(c.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    def _render(self, c):
        img = QImage(c.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        c.paint_scene(p)
        p.end()
        return img

    def _has_color_near(self, img, x, y, r=30, pred=lambda col: col.alpha() > 100):
        for dx in range(-r, r + 1, 3):
            for dy in range(-r, r + 1, 3):
                px, py = x + dx, y + dy
                if 0 <= px < img.width() and 0 <= py < img.height() and pred(QColor.fromRgba(img.pixel(px, py))):
                    return True
        return False

    def test_empty_is_fully_transparent(self):
        c = self.make()
        img = self._render(c)
        self.assertFalse(self._has_color_near(img, 500, 350, r=300))

    def test_draws_and_clears(self):
        c = self.make()
        c._origin = (0, 0)
        c._dpr = 1.0
        c.apply_commands(validate_batch([
            {"type": "cursor_point", "x": 200, "y": 200, "label": "Go"},
            {"type": "box_highlight", "x": 500, "y": 300, "w": 200, "h": 100, "color": "cyan"},
            {"type": "arrow_connect", "x1": 250, "y1": 250, "x2": 480, "y2": 330},
            {"type": "freehand_circle", "cx": 800, "cy": 500, "rx": 60, "ry": 40},
            {"type": "step_hud", "step": 1, "total": 3},
        ]))
        c._items = [(i, t - 5) for i, t in c._items]  # skip circle sweep animation
        img = self._render(c)
        red = lambda col: col.alpha() > 100 and col.red() > 200 and col.green() < 120
        cyan = lambda col: col.alpha() > 100 and col.blue() > 200 and col.red() < 80
        self.assertTrue(self._has_color_near(img, 200, 200, 40, red), "red cursor")
        self.assertTrue(self._has_color_near(img, 500, 350, 6, cyan), "box left edge")
        self.assertTrue(self._has_color_near(img, 800, 460, 12, red), "circle top")
        c.clear()
        self.assertFalse(self._has_color_near(self._render(c), 200, 200, 300))

    def test_one_cursor_at_a_time(self):
        c = self.make()
        c.apply_commands(validate_batch([{"type": "cursor_point", "x": 1, "y": 1}]))
        c.apply_commands(validate_batch([{"type": "cursor_point", "x": 9, "y": 9}]))
        self.assertEqual(len(c._items), 1)
        self.assertEqual(c._items[0][0].data["x"], 9.0)

    def test_dpr_mapping(self):
        c = self.make()
        c._dpr, c._origin = 2.0, (10, 20)
        p = c.to_local(400, 200)
        self.assertEqual((p.x(), p.y()), (190.0, 80.0))


if __name__ == "__main__":
    unittest.main()
