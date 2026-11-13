"""Regenerates the Android launcher icons, adaptive-icon layers and splash screens (Nyro ring logo).
Run from mobile/:  python3 scripts/brand_android.py      (needs Pillow)"""
from pathlib import Path
from PIL import Image, ImageDraw

RES = Path(__file__).resolve().parent.parent / "android/app/src/main/res"
BG, RED = (33, 33, 33, 255), (232, 51, 74, 255)
SS = 4  # supersampling for smooth edges


def logo(size: int, scale: float) -> Image.Image:
    """Transparent square with the ring+dot logo; `scale` = ring diameter as a fraction of the canvas."""
    big = size * SS
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c, r = big / 2, big * scale / 2
    w = big * scale * 0.14
    d.ellipse((c - r, c - r, c + r, c + r), outline=RED, width=int(w))
    dot = big * scale * 0.17
    d.ellipse((c - dot, c - dot, c + dot, c + dot), fill=RED)
    return im.resize((size, size), Image.LANCZOS)


def legacy(size: int, round_: bool) -> Image.Image:
    base = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    box = (0, 0, size * SS - 1, size * SS - 1)
    if round_:
        d.ellipse(box, fill=BG)
    else:
        d.rounded_rectangle(box, radius=int(size * SS * 0.22), fill=BG)
    base = base.resize((size, size), Image.LANCZOS)
    base.alpha_composite(logo(size, 0.56))
    return base


def main() -> None:
    dens = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
    for name, f in dens.items():
        d = RES / f"mipmap-{name}"
        fg = int(108 * f)
        logo(fg, 0.40).save(d / "ic_launcher_foreground.png")      # inside the 66/108 adaptive safe zone
        legacy(int(48 * f), False).save(d / "ic_launcher.png")
        legacy(int(48 * f), True).save(d / "ic_launcher_round.png")
    for p in RES.glob("drawable*/splash.png"):
        w, h = Image.open(p).size
        im = Image.new("RGBA", (w, h), BG)
        s = int(min(w, h) * 0.26)
        im.alpha_composite(logo(s, 0.9), ((w - s) // 2, (h - s) // 2))
        im.convert("RGB").save(p)
    (RES / "values/ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">#212121</color>\n</resources>\n')
    (RES / "values/colors.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="colorPrimary">#212121</color>\n'
        '    <color name="colorPrimaryDark">#171717</color>\n    <color name="colorAccent">#E8334A</color>\n</resources>\n')
    print("branding applied")


if __name__ == "__main__":
    main()
