"""Run on Windows: draws a red cursor, box, arrow, circle and step HUD.

Terminal 1:  python -m host.overlay.screen_canvas
Terminal 2:  python scripts/demo_overlay.py
Clear with Ctrl+Shift+X. Click through the drawings: they must not block the mouse.
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765"


def post(path: str, body: dict | list | None = None) -> None:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    print(path, urllib.request.urlopen(req, timeout=3).read().decode())


def main() -> int:
    try:
        urllib.request.urlopen(URL + "/health", timeout=2)
    except Exception:
        print("Overlay not running. Start: python -m host.overlay.screen_canvas")
        return 1
    post("/draw", {"commands": [
        {"type": "step_hud", "step": 1, "total": 3, "label": "Open the Media tab"},
        {"type": "cursor_point", "x": 400, "y": 300, "label": "Click here"},
        {"type": "box_highlight", "x": 600, "y": 200, "w": 320, "h": 140, "label": "This panel", "color": "cyan"},
        {"type": "arrow_connect", "x1": 420, "y1": 320, "x2": 600, "y2": 270, "label": "then here", "color": "yellow"},
        {"type": "freehand_circle", "cx": 900, "cy": 500, "rx": 90, "ry": 50, "label": "Check this"},
    ]})
    print("Drawn. Clearing in 10s (or press Ctrl+Shift+X).")
    time.sleep(10)
    post("/clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
