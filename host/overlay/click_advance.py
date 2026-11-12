"""Click-to-advance: fire a callback when the user clicks inside a target box."""
from __future__ import annotations

import threading
from typing import Callable

Box = tuple[float, float, float, float]  # x, y, w, h in native desktop pixels


def hit_test(box: Box, x: float, y: float, pad: float = 0.0) -> bool:
    bx, by, bw, bh = box
    return bx - pad <= x <= bx + bw + pad and by - pad <= y <= by + bh + pad


class ClickAdvance:
    """Arm with a box + callback. First left-click inside the box fires once.

    `feed_click` is the testable core; `start()` wires it to a pynput listener.
    """

    def __init__(self, on_hit: Callable[[], None], pad: float = 6.0):
        self._on_hit = on_hit
        self._pad = pad
        self._box: Box | None = None
        self._lock = threading.Lock()
        self._listener = None

    def arm(self, box: Box) -> None:
        with self._lock:
            self._box = box

    def disarm(self) -> None:
        with self._lock:
            self._box = None

    @property
    def armed(self) -> bool:
        return self._box is not None

    def feed_click(self, x: float, y: float, pressed: bool = True, left: bool = True) -> bool:
        if not pressed or not left:
            return False
        with self._lock:
            box = self._box
            if box is None or not hit_test(box, x, y, self._pad):
                return False
            self._box = None  # one-shot
        self._on_hit()
        return True

    def start(self) -> None:
        from pynput import mouse  # lazy: needs a display

        def _on_click(x, y, button, pressed):
            self.feed_click(x, y, pressed, button == mouse.Button.left)

        self._listener = mouse.Listener(on_click=_on_click)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
