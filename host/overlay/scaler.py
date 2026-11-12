"""800px WebP downscaler and coordinate mapper (pure logic + Pillow)."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

TARGET_WIDTH = 800
WEBP_QUALITY = 75


@dataclass(frozen=True)
class ScaledCapture:
    webp: bytes
    scaled_w: int
    scaled_h: int
    orig_w: int
    orig_h: int
    origin_x: int = 0   # native desktop position of the capture's top-left
    origin_y: int = 0

    @property
    def scale_factor(self) -> float:
        return self.orig_w / self.scaled_w


def downscale_to_webp(img: Image.Image, origin: tuple[int, int] = (0, 0),
                      target_width: int = TARGET_WIDTH,
                      quality: int = WEBP_QUALITY) -> ScaledCapture:
    """Resize to target_width (keep aspect) and encode as WebP."""
    orig_w, orig_h = img.size
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError("Empty image")
    scaled_w = min(target_width, orig_w)  # never upscale
    scaled_h = max(1, round(orig_h * scaled_w / orig_w))
    small = img.convert("RGB").resize((scaled_w, scaled_h), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="WEBP", quality=quality, method=4)
    return ScaledCapture(buf.getvalue(), scaled_w, scaled_h, orig_w, orig_h, origin[0], origin[1])


def point_to_native(cap: ScaledCapture, x: float, y: float) -> tuple[int, int]:
    s = cap.scale_factor
    return round(cap.origin_x + x * s), round(cap.origin_y + y * s)


def box_to_native(cap: ScaledCapture, x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    s = cap.scale_factor
    return round(cap.origin_x + x * s), round(cap.origin_y + y * s), round(w * s), round(h * s)


def clamp_scaled(cap: ScaledCapture, x: float, y: float) -> tuple[float, float]:
    """Clamp model-returned coordinates into the image (models sometimes overshoot)."""
    return min(max(x, 0.0), float(cap.scaled_w)), min(max(y, 0.0), float(cap.scaled_h))
