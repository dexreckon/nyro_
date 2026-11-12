import io
import unittest

from PIL import Image

from host.overlay.scaler import (box_to_native, clamp_scaled, downscale_to_webp, point_to_native)


class ScalerTests(unittest.TestCase):
    def test_1080p_to_800x450(self):
        cap = downscale_to_webp(Image.new("RGB", (1920, 1080), (10, 20, 30)))
        self.assertEqual((cap.scaled_w, cap.scaled_h), (800, 450))
        self.assertAlmostEqual(cap.scale_factor, 2.4)
        img = Image.open(io.BytesIO(cap.webp))
        self.assertEqual(img.format, "WEBP")
        self.assertEqual(img.size, (800, 450))

    def test_4k_scale(self):
        cap = downscale_to_webp(Image.new("RGB", (3840, 2160)))
        self.assertAlmostEqual(cap.scale_factor, 4.8)

    def test_no_upscale(self):
        cap = downscale_to_webp(Image.new("RGB", (400, 300)))
        self.assertEqual((cap.scaled_w, cap.scaled_h), (400, 300))
        self.assertEqual(cap.scale_factor, 1.0)

    def test_point_mapping_with_origin(self):
        cap = downscale_to_webp(Image.new("RGB", (1600, 900)), origin=(100, 50))
        self.assertEqual(point_to_native(cap, 400, 225), (100 + 800, 50 + 450))

    def test_box_mapping(self):
        cap = downscale_to_webp(Image.new("RGB", (1920, 1080)))
        self.assertEqual(box_to_native(cap, 100, 50, 80, 20), (240, 120, 192, 48))

    def test_clamp(self):
        cap = downscale_to_webp(Image.new("RGB", (1920, 1080)))
        self.assertEqual(clamp_scaled(cap, 900, -5), (800.0, 0.0))

    def test_empty_rejected(self):
        with self.assertRaises(Exception):
            downscale_to_webp(Image.new("RGB", (0, 10)))

    def test_small_payload(self):
        cap = downscale_to_webp(Image.new("RGB", (1920, 1080), (200, 200, 200)))
        self.assertLess(len(cap.webp), 20_000)


if __name__ == "__main__":
    unittest.main()
