"""Universal click-through transparent vector overlay (PyQt6)."""
from __future__ import annotations

import math
import sys
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QPainter,
                         QPainterPath, QPen, QPolygonF)
from PyQt6.QtWidgets import QApplication, QMainWindow

from .commands import DrawCommand

COLORS = {
    "red": QColor(255, 40, 60),
    "cyan": QColor(0, 220, 255),
    "yellow": QColor(255, 210, 0),
    "green": QColor(60, 230, 120),
}
MAX_ITEMS = 64


class ScreenCanvas(QMainWindow):
    """Full-virtual-desktop overlay. All mouse input passes through to apps below."""

    commands_received = pyqtSignal(list)  # thread-safe entry point (queued)
    clear_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        screen = QGuiApplication.primaryScreen()
        self._dpr = screen.devicePixelRatio() if screen else 1.0
        geo = screen.virtualGeometry() if screen else self.geometry()
        self.setGeometry(geo)
        self._origin = (geo.x(), geo.y())

        self._items: list[tuple[DrawCommand, float]] = []  # (command, birth time)
        self.commands_received.connect(self.apply_commands)
        self.clear_requested.connect(self.clear)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(33)

    # ------------------------------------------------------------ state
    def apply_commands(self, cmds: list) -> None:
        now = time.monotonic()
        for c in cmds:
            if c.kind == "clear":
                self._items.clear()
                continue
            if c.kind in ("cursor_point", "step_hud"):  # only one of each at a time
                self._items = [(i, t) for i, t in self._items if i.kind != c.kind]
            self._items.append((c, now))
        del self._items[:-MAX_ITEMS]
        self.update()

    def clear(self) -> None:
        self._items.clear()
        self.update()

    # ------------------------------------------------------- coordinates
    def to_local(self, x: float, y: float) -> QPointF:
        """Native physical desktop pixels -> overlay-local logical pixels."""
        return QPointF(x / self._dpr - self._origin[0], y / self._dpr - self._origin[1])

    def _len(self, v: float) -> float:
        return v / self._dpr

    # ------------------------------------------------------------ paint
    def paintEvent(self, _event):
        p = QPainter(self)
        self.paint_scene(p)
        p.end()

    def paint_scene(self, p: QPainter) -> None:
        """Draw all items onto any painter (the window, or an image in tests)."""
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        phase = time.monotonic()
        for cmd, born in self._items:
            d, col = cmd.data, COLORS.get(cmd.data.get("color", "red"), COLORS["red"])
            if cmd.kind == "cursor_point":
                self._draw_cursor(p, self.to_local(d["x"], d["y"]), col, d["label"], phase)
            elif cmd.kind == "box_highlight":
                tl = self.to_local(d["x"], d["y"])
                r = QRectF(tl.x(), tl.y(), self._len(d["w"]), self._len(d["h"]))
                self._draw_box(p, r, col, d["label"], phase)
            elif cmd.kind == "arrow_connect":
                self._draw_arrow(p, self.to_local(d["x1"], d["y1"]), self.to_local(d["x2"], d["y2"]), col, d["label"])
            elif cmd.kind == "freehand_circle":
                c = self.to_local(d["cx"], d["cy"])
                self._draw_circle(p, c, self._len(d["rx"]), self._len(d["ry"]), col, d["label"], phase - born)
            elif cmd.kind == "step_hud":
                self._draw_hud(p, d["step"], d["total"], d["label"])

    # -------------------------------------------------------- primitives
    @staticmethod
    def _glow_pen(p: QPainter, col: QColor, width: float, passes: int = 3):
        """Yield pens from wide/faint to narrow/solid for a glow effect."""
        for i in range(passes, 0, -1):
            c = QColor(col)
            c.setAlpha(int(40 * (passes - i + 1)))
            yield QPen(c, width + i * 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        yield QPen(col, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def _pill(self, p: QPainter, anchor: QPointF, text: str, col: QColor) -> None:
        if not text:
            return
        font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 22, fm.height() + 10
        screen_w = self.width()
        x = min(max(anchor.x(), 6), screen_w - w - 6)
        y = max(anchor.y(), 6)
        rect = QRectF(x, y, w, h)
        bg = QColor(20, 20, 28, 225)
        p.setPen(QPen(col, 2))
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_cursor(self, p, tip: QPointF, col, label, phase):
        pulse = 0.5 + 0.5 * math.sin(phase * 4)
        ring_r = 16 + 10 * pulse
        ring = QColor(col)
        ring.setAlpha(int(200 - 120 * pulse))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ring, 3))
        p.drawEllipse(tip, ring_r, ring_r)
        # arrow glyph pointing up-left at the tip
        pts = QPolygonF([
            tip, tip + QPointF(0, 30), tip + QPointF(8, 23),
            tip + QPointF(15, 36), tip + QPointF(21, 33), tip + QPointF(14, 21), tip + QPointF(24, 20),
        ])
        for pen in self._glow_pen(p, col, 1.5, 2):
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(pts)
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.setBrush(col)
        p.drawPolygon(pts)
        self._pill(p, tip + QPointF(28, 36), label, col)

    def _draw_box(self, p, rect: QRectF, col, label, phase):
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pen in self._glow_pen(p, col, 3):
            p.setPen(pen)
            p.drawRoundedRect(rect, 10, 10)
        self._pill(p, QPointF(rect.x(), rect.y() - 34), label, col)

    def _draw_arrow(self, p, a: QPointF, b: QPointF, col, label):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        dist = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / dist, dx / dist
        ctrl = QPointF((a.x() + b.x()) / 2 + nx * dist * 0.15, (a.y() + b.y()) / 2 + ny * dist * 0.15)
        path = QPainterPath(a)
        path.quadTo(ctrl, b)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pen in self._glow_pen(p, col, 4):
            p.setPen(pen)
            p.drawPath(path)
        ang = math.atan2(b.y() - ctrl.y(), b.x() - ctrl.x())
        head = QPolygonF([
            b,
            QPointF(b.x() - 18 * math.cos(ang - 0.45), b.y() - 18 * math.sin(ang - 0.45)),
            QPointF(b.x() - 18 * math.cos(ang + 0.45), b.y() - 18 * math.sin(ang + 0.45)),
        ])
        p.setPen(QPen(col, 2))
        p.setBrush(col)
        p.drawPolygon(head)
        self._pill(p, QPointF((a.x() + b.x()) / 2 + 10, (a.y() + b.y()) / 2 + 10), label, col)

    def _draw_circle(self, p, c: QPointF, rx, ry, col, label, age):
        reveal = min(age / 0.5, 1.0)  # hand-drawn sweep over 0.5s
        path = QPainterPath()
        steps = 90
        total = 2.15 * math.pi * reveal
        n = max(2, int(steps * reveal))
        for i in range(n + 1):
            t = total * i / n
            k = 1.0 + 0.06 * (t / (2.15 * math.pi))  # slight spiral like a pen stroke
            pt = QPointF(c.x() + rx * k * math.cos(t - math.pi / 2), c.y() + ry * k * math.sin(t - math.pi / 2))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pen in self._glow_pen(p, col, 4):
            p.setPen(pen)
            p.drawPath(path)
        self._pill(p, QPointF(c.x() - rx, c.y() - ry - 38), label, col)

    def _draw_hud(self, p, step, total, label):
        text = f"Step {step}/{total}" + (f" - {label}" if label else "")
        font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        p.setFont(font)
        fm = QFontMetricsF(font)
        w, h = fm.horizontalAdvance(text) + 28, fm.height() + 14
        rect = QRectF(self.width() - w - 20, 20, w, h)
        p.setPen(QPen(COLORS["cyan"], 2))
        p.setBrush(QColor(15, 15, 25, 230))
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def _hotkey_to_pynput(s: str) -> str:
    parts = [x.strip().lower() for x in s.split("+")]
    return "+".join(f"<{x}>" if len(x) > 1 else x for x in parts)


def run(host: str = "127.0.0.1", port: int = 8765, hotkey: str = "ctrl+shift+x") -> int:
    """Start overlay + HTTP bridge + global clear hotkey. Blocks until quit."""
    from .bridge import OverlayBridge

    app = QApplication.instance() or QApplication(sys.argv)
    canvas = ScreenCanvas()
    canvas.show()
    bridge = OverlayBridge(lambda cmds: canvas.commands_received.emit(cmds), host, port)
    bridge.start()

    listener = None
    try:
        from pynput import keyboard
        listener = keyboard.GlobalHotKeys({_hotkey_to_pynput(hotkey): canvas.clear_requested.emit})
        listener.daemon = True
        listener.start()
    except Exception as e:  # no display / permissions: overlay still works
        print(f"[overlay] global hotkey unavailable: {e}", file=sys.stderr)

    print(f"[overlay] listening on http://{host}:{bridge.port}  (clear: {hotkey})")
    try:
        return app.exec()
    finally:
        bridge.stop()
        if listener:
            listener.stop()


if __name__ == "__main__":
    from host.config import load_config

    ov = load_config()["overlay"]
    sys.exit(run(ov["bridge_host"], ov["bridge_port"], ov["clear_hotkey"]))
