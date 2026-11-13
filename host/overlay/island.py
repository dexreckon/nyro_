"""Nyro "Dynamic Island": a pill at the top-centre of the screen that springs open while you talk to Nyro.

States (sent as JSON datagrams to 127.0.0.1:<port>):  {"state": "listening"|"thinking"|"done"|"error"|"hide", "text": "..."}
and live microphone level while listening:             {"level": 0.0-1.0}

Run:  python -m host.overlay.island            (listens for the host)
      python -m host.overlay.island --demo     (cycles through the states by itself)
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter,
                         QPainterPath, QPen)
from PyQt6.QtNetwork import QHostAddress, QUdpSocket
from PyQt6.QtWidgets import QApplication, QWidget

WIN_W, WIN_H = 560, 110      # window is bigger than the pill so the spring can overshoot
TOP_MARGIN = 10
COLLAPSED = (64.0, 20.0)     # the little capsule it grows out of / shrinks back into
HOLD = {"listening": 25.0, "thinking": 120.0, "error": 4.0}   # failsafe auto-hide (seconds)
BG = QColor(4, 4, 6)
ACCENT = QColor(255, 55, 95)
GREEN = QColor(48, 209, 88)


def spring(x: float, v: float, target: float, dt: float, k: float = 300.0, c: float = 25.0) -> tuple[float, float]:
    """One step of a slightly under-damped spring (gives the iPhone-style overshoot)."""
    v += (k * (target - x) - c * v) * dt
    return x + v * dt, v


class Island(QWidget):
    BARS = 40

    def __init__(self, port: int = 8767, bind: bool = True):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(WIN_W, WIN_H)

        self.state, self.text = "hidden", ""
        self.w, self.h = COLLAPSED
        self.vw = self.vh = 0.0
        self.tw, self.th = COLLAPSED
        self.alpha, self.talpha = 0.0, 0.0
        self.level_target, self.level = 0.0, 0.0
        self.last_level_t = 0.0
        self.hist = [0.0] * self.BARS
        self._hist_acc = 0.0
        self.phase = 0.0
        self.deadline: float | None = None
        self._font = QFont("Segoe UI", 10)
        self._font.setWeight(QFont.Weight.DemiBold)
        self._last = time.monotonic()

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._on_timer)

        self.sock: QUdpSocket | None = None
        if bind:
            self.sock = QUdpSocket(self)
            if self.sock.bind(QHostAddress.SpecialAddress.LocalHost, port):
                self.sock.readyRead.connect(self._read)
            else:
                print(f"[island] could not bind UDP port {port}", file=sys.stderr)

    # ------------------------------------------------------------ input
    def _read(self) -> None:
        while self.sock and self.sock.hasPendingDatagrams():
            data = bytes(self.sock.receiveDatagram().data())
            try:
                msg = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "level" in msg:
                self.set_level(float(msg.get("level") or 0.0))
            if "state" in msg:
                self.set_state(str(msg["state"]), str(msg.get("text") or ""))

    def set_level(self, v: float) -> None:
        self.level_target = max(0.0, min(1.0, v))
        self.last_level_t = time.monotonic()

    def set_state(self, state: str, text: str = "") -> None:
        now = time.monotonic()
        text = " ".join(text.split())[:240]
        if state == "hide" or state not in ("listening", "thinking", "done", "error"):
            self.state, self.talpha = "hidden", 0.0
            self.tw, self.th = COLLAPSED
            self.deadline = None
            return
        if self.state == "hidden" and self.alpha < 0.05:
            self._place()
            self.w, self.h = COLLAPSED
            self.vw = self.vh = 0.0
            self.hist = [0.0] * self.BARS
        self.state, self.text, self.talpha = state, text, 1.0
        self.tw, self.th = self._target_size()
        if state == "done":
            self.deadline = now + max(2.2, min(7.0, 1.2 + len(text) * 0.045))
        else:
            self.deadline = now + HOLD.get(state, 10.0)
        self._last = now
        if not self.isVisible():
            self.show()
        if not self._timer.isActive():
            self._timer.start()

    # ------------------------------------------------------------ layout
    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen:
            g = screen.geometry()
            self.move(g.x() + (g.width() - WIN_W) // 2, g.y())

    def _target_size(self) -> tuple[float, float]:
        fm = QFontMetricsF(self._font)
        if self.state == "listening":
            return 320.0, 58.0
        label = self.text or ("Thinking…" if self.state == "thinking" else "")
        tw = fm.horizontalAdvance(label)
        if self.state == "thinking":
            return max(190.0, min(470.0, tw + 112.0)), 56.0
        return max(200.0, min(500.0, tw + 100.0)), 56.0

    # ------------------------------------------------------------ animation
    def _on_timer(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._last))
        self._last = now
        self.step(dt, now)

    def step(self, dt: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.phase += dt
        self.w, self.vw = spring(self.w, self.vw, self.tw, dt)
        self.h, self.vh = spring(self.h, self.vh, self.th, dt)
        a_rate = 9.0 if self.talpha > self.alpha else 7.0
        self.alpha += (self.talpha - self.alpha) * min(1.0, dt * a_rate)

        # mic level: fast attack, slow release; fades to a gentle "breathing" when no data arrives
        tgt = self.level_target
        if now - self.last_level_t > 0.35:
            tgt = 0.05 + 0.03 * math.sin(self.phase * 2.0)
            self.level_target *= 0.9
        rate = 0.6 if tgt > self.level else 0.12
        self.level += (tgt - self.level) * min(1.0, rate * dt * 60.0)
        self._hist_acc += dt
        while self._hist_acc >= 1 / 30:
            self._hist_acc -= 1 / 30
            self.hist.append(self.level)
            del self.hist[0]

        if self.deadline is not None and now > self.deadline and self.state != "hidden":
            self.set_state("hide")
        if self.state == "hidden" and self.alpha < 0.02:
            self.alpha = 0.0
            self._timer.stop()
            self.hide()
            return
        self.update()

    # ------------------------------------------------------------ drawing
    def paintEvent(self, _event) -> None:
        if self.alpha <= 0.01 or self.w < 2 or self.h < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = self.w, self.h
        x, y = (WIN_W - w) / 2, float(TOP_MARGIN)
        rect = QRectF(x, y, w, h)
        radius = min(h / 2, 29.0)
        p.setOpacity(self.alpha)

        for i in range(5, 0, -1):  # soft drop shadow
            c = QColor(0, 0, 0, 14)
            p.setBrush(QBrush(c))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect.adjusted(-i, 2 - i, i, i * 2 + 2), radius + i, radius + i)

        pill = QPainterPath()
        pill.addRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.setBrush(QBrush(BG))
        p.drawPath(pill)

        # content fades in as the pill nears its target size
        prog = 1.0 if self.tw <= 0 else (w - 0.72 * self.tw) / (0.28 * self.tw)
        content = max(0.0, min(1.0, prog))
        if content > 0.02 and self.state != "hidden":
            p.setClipPath(pill)
            p.setOpacity(self.alpha * content)
            {"listening": self._draw_listening, "thinking": self._draw_thinking,
             "done": self._draw_done, "error": self._draw_done}[self.state](p, rect)
        p.end()

    def _elide(self, text: str, width: float) -> str:
        return QFontMetricsF(self._font).elidedText(text, Qt.TextElideMode.ElideRight, int(width))

    def _draw_text(self, p: QPainter, text: str, rect: QRectF, color=QColor(255, 255, 255, 235)) -> None:
        p.setFont(self._font)
        p.setPen(color)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)

    def _draw_listening(self, p: QPainter, r: QRectF) -> None:
        cy = r.center().y()
        pulse = 5.0 + 1.6 * math.sin(self.phase * 5.0) + 3.0 * self.level
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 60))
        p.drawEllipse(QPointF(r.left() + 26, cy), pulse + 4, pulse + 4)
        p.setBrush(ACCENT)
        p.drawEllipse(QPointF(r.left() + 26, cy), 5.0, 5.0)

        self._draw_text(p, "Listening", QRectF(r.left() + 42, r.top(), 70, r.height()), QColor(255, 255, 255, 190))

        left, right = r.left() + 118, r.right() - 24
        n = max(4, min(self.BARS, int((right - left) / 6.0)))
        grad = QLinearGradient(left, 0, right, 0)
        grad.setColorAt(0.0, QColor(90, 200, 250))
        grad.setColorAt(0.5, QColor(191, 90, 242))
        grad.setColorAt(1.0, QColor(255, 55, 95))
        p.setBrush(QBrush(grad))
        step = (right - left) / n
        max_h = r.height() - 22
        for i in range(n):
            v = self.hist[-n + i]
            wobble = 0.78 + 0.22 * math.sin(self.phase * 7.0 + i * 0.85)
            bh = 4.0 + max(0.0, v) * wobble * (max_h - 4.0)
            bx = left + i * step + step * 0.18
            p.drawRoundedRect(QRectF(bx, cy - bh / 2, step * 0.64, bh), step * 0.32, step * 0.32)

    def _draw_thinking(self, p: QPainter, r: QRectF) -> None:
        cy = r.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        for i in range(3):
            bounce = -5.0 * max(0.0, math.sin(self.phase * 6.0 - i * 0.9))
            p.drawEllipse(QPointF(r.left() + 30 + i * 13, cy + bounce), 3.6, 3.6)
        label = f"“{self.text}”" if self.text else "Thinking…"
        self._draw_text(p, self._elide(label, r.width() - 96), QRectF(r.left() + 78, r.top(), r.width() - 90, r.height()))

    def _draw_done(self, p: QPainter, r: QRectF) -> None:
        cy = r.center().y()
        ok = self.state == "done"
        col = GREEN if ok else QColor(255, 69, 58)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        c = QPointF(r.left() + 28, cy)
        p.drawEllipse(c, 11.0, 11.0)
        pen = QPen(QColor(0, 0, 0), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if ok:
            path = QPainterPath(QPointF(c.x() - 4.5, c.y() + 0.5))
            path.lineTo(c.x() - 1.2, c.y() + 4.0)
            path.lineTo(c.x() + 5.0, c.y() - 3.8)
            p.drawPath(path)
        else:
            p.drawLine(QPointF(c.x(), c.y() - 4.5), QPointF(c.x(), c.y() + 1.0))
            p.drawPoint(QPointF(c.x(), c.y() + 4.6))
        label = self.text or ("Done" if ok else "Something went wrong")
        self._draw_text(p, self._elide(label, r.width() - 72), QRectF(r.left() + 52, r.top(), r.width() - 64, r.height()))

    # ------------------------------------------------------------ demo
    def start_demo(self) -> None:
        """Cycle through every state forever (for trying the look without the rest of Nyro)."""
        script = [("listening", "", 4.2), ("thinking", "open youtube in chrome", 1.6),
                  ("done", "Opening youtube in chrome.", 3.2), ("hide", "", 1.5),
                  ("listening", "", 3.0), ("thinking", "", 1.2),
                  ("error", "The daily AI limit is reached.", 3.5), ("hide", "", 1.5)]
        idx = {"i": 0}
        t0 = {"t": time.monotonic()}

        def advance() -> None:
            state, text, dur = script[idx["i"] % len(script)]
            self.set_state(state, text)
            t0["t"] = time.monotonic()
            QTimer.singleShot(int(dur * 1000), advance)
            idx["i"] += 1

        def fake_voice() -> None:
            if self.state == "listening":
                t = time.monotonic()
                burst = max(0.0, math.sin(t * 3.1)) * (0.5 + 0.5 * math.sin(t * 1.3))
                self.set_level(min(1.0, 0.08 + burst * 0.9 * random.uniform(0.6, 1.0)))

        advance()
        voice = QTimer(self)
        voice.timeout.connect(fake_voice)
        voice.start(50)
        self._demo_voice = voice


def render_preview(out_dir: str) -> list[str]:
    """Render each state to a PNG without a real display (QT_QPA_PLATFORM=offscreen). Used for visual checks."""
    os.makedirs(out_dir, exist_ok=True)
    isl = Island(bind=False)
    paths = []
    for name, state, text in [("1_listening", "listening", ""), ("2_thinking", "thinking", "open youtube in chrome"),
                              ("3_done", "done", "Opening youtube in chrome."),
                              ("4_error", "error", "The daily AI limit is reached.")]:
        isl.set_state(state, text)
        for k in range(120):
            if state == "listening":
                isl.set_level(0.15 + 0.8 * abs(math.sin(k / 6.0)) * abs(math.sin(k / 17.0 + 1)))
            isl.step(1 / 60, now=time.monotonic())
            isl.deadline = None
        path = os.path.join(out_dir, f"{name}.png")
        isl.grab().save(path)
        paths.append(path)
    return paths


def run(port: int = 8767, demo: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    isl = Island(port)
    if demo:
        isl.start_demo()
    print(f"[island] listening on udp://127.0.0.1:{port}", flush=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run(int(os.environ.get("NYRO_ISLAND_PORT", "8767")), demo="--demo" in sys.argv))
