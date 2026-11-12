import unittest

from host.overlay.click_advance import ClickAdvance, hit_test
from host.overlay.commands import CommandError, validate_batch, validate_command


class CommandTests(unittest.TestCase):
    def test_cursor(self):
        c = validate_command({"type": "cursor_point", "x": 10, "y": 20, "label": "hi"})
        self.assertEqual((c.kind, c.data["x"], c.data["color"]), ("cursor_point", 10.0, "red"))

    def test_batch_forms(self):
        one = {"type": "clear"}
        self.assertEqual(len(validate_batch(one)), 1)
        self.assertEqual(len(validate_batch([one, one])), 2)
        self.assertEqual(len(validate_batch({"commands": [one]})), 1)

    def test_rejects(self):
        bad = [
            {"type": "nope"}, {"type": "cursor_point", "x": "1", "y": 2},
            {"type": "cursor_point", "x": True, "y": 2},
            {"type": "cursor_point", "x": 10 ** 9, "y": 2},
            {"type": "box_highlight", "x": 0, "y": 0, "w": 0, "h": 5},
            {"type": "cursor_point", "x": 1, "y": 1, "color": "pink"},
            {"type": "step_hud", "step": 5, "total": 3}, "str", 5,
        ]
        for b in bad:
            with self.assertRaises(CommandError, msg=str(b)):
                validate_batch(b)

    def test_label_truncated(self):
        c = validate_command({"type": "cursor_point", "x": 1, "y": 1, "label": "x" * 999})
        self.assertEqual(len(c.data["label"]), 120)

    def test_too_many(self):
        with self.assertRaises(CommandError):
            validate_batch([{"type": "clear"}] * 65)


class ClickTests(unittest.TestCase):
    def test_hit(self):
        self.assertTrue(hit_test((10, 10, 100, 50), 50, 30))
        self.assertFalse(hit_test((10, 10, 100, 50), 200, 30))
        self.assertTrue(hit_test((10, 10, 100, 50), 113, 30, pad=6))

    def test_one_shot_advance(self):
        hits = []
        ca = ClickAdvance(lambda: hits.append(1), pad=0)
        self.assertFalse(ca.feed_click(50, 30))  # not armed
        ca.arm((10, 10, 100, 50))
        self.assertFalse(ca.feed_click(500, 500))       # outside
        self.assertFalse(ca.feed_click(50, 30, pressed=False))  # release
        self.assertFalse(ca.feed_click(50, 30, left=False))     # right-click
        self.assertTrue(ca.feed_click(50, 30))
        self.assertFalse(ca.feed_click(50, 30))  # disarmed after firing
        self.assertEqual(hits, [1])
        self.assertFalse(ca.armed)


if __name__ == "__main__":
    unittest.main()
