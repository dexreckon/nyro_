"""DEV ONLY: a stand-in 'laptop' that plays realistic Nyro conversations into the relay, so every UI state can be
seen and screenshotted without the real host. Run the relay first, then:  python scripts/mock_host.py  (token: env NYRO_PAIR_TOKEN or ./.token)"""
import base64
import io
import json
import sys
import threading
import time
from pathlib import Path

from websockets.sync.client import connect

import os
TOKEN = (os.environ.get("NYRO_PAIR_TOKEN") or (Path(__file__).resolve().parent.parent / ".token").read_text()).strip()
URL = "ws://127.0.0.1:8000/ws/host"


def fake_screen() -> str:
    """A believable little screenshot (YouTube-ish page) for the 'What Nyro sees' thumbnail."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (800, 500), (24, 24, 28))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 800, 44), fill=(36, 36, 42))
    d.rounded_rectangle((250, 9, 560, 35), 13, fill=(58, 58, 66))
    d.rounded_rectangle((14, 62, 566, 360), 12, fill=(52, 40, 74))
    d.polygon([(260, 160), (260, 260), (350, 210)], fill=(255, 255, 255))
    for i in range(4):
        d.rounded_rectangle((590, 62 + i * 100, 786, 140 + i * 100), 8, fill=(40, 40, 48))
    d.rectangle((14, 376, 400, 392), fill=(70, 70, 80))
    d.rectangle((14, 402, 300, 414), fill=(50, 50, 58))
    d.rounded_rectangle((300, 150, 372, 270), 10, outline=(255, 55, 95), width=5)   # the 'annotation'
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Mock:
    def __init__(self, ws):
        self.ws, self.lock, self.approvals, self.next_id = ws, threading.Lock(), {}, 100
        self.used = 12

    def send(self, obj):
        with self.lock:
            self.ws.send(json.dumps(obj))

    def ev(self, event, **kw):
        self.send({"type": "event", "event": event, **kw})

    def budget(self):
        self.ev("budget", used=self.used, cap=400, warn=False, exhausted=False, pace_warn=False)

    def stream(self, text, step=0.025, chunk=3):
        for i in range(0, len(text), chunk):
            self.ev("text", text=text[i:i + chunk])
            time.sleep(step)

    def reply(self, text, source="llm", persona=None):
        self.send({"type": "reply", "text": text, "source": source, "persona": persona, "budget": f"{self.used}/400"})

    def tools(self, names, gap=0.7):
        for n in names:
            self.ev("tool", name=n)
            time.sleep(gap)

    # ---- scenarios
    def handle(self, text):
        t = text.lower()
        self.send({"type": "heard", "text": text})
        time.sleep(0.3)
        if "organi" in t or "tidy" in t or "clean up" in t:
            return self.organize()
        if t.startswith("find") or "where is" in t:
            return self.reply("Found assignment.pdf in University. Also: Lab_Mid_BCS_5D.pdf, widgets assignment 179.pdf.",
                              "local", "filemanager")
        if "volume" in t:
            return self.reply("Volume 30 percent.", "local", "sysop")
        if "battery" in t:
            return self.reply("CPU 4%, RAM 60%, Battery 43% (charging)", "local", "sysop")
        if "python" in t or "file" in t or ".cpp" in t or "dart" in t or "vs code" in t:
            return self.coder()
        if "explain" in t or "guide" in t or "help me" in t:
            return self.explain()
        if "error" in t:
            return self.ev("error", error="claude CLI not found (is Claude Code installed and on PATH?)")
        if "close" in t and "tab" in t:
            return self.reply("Closed the 'YouTube' tab.", "local", "sysop")
        return self.act()

    def organize(self):
        self.used += 0
        aid = self.next_id
        self.next_id += 1
        done = threading.Event()
        self.approvals[aid] = {"event": done, "ok": None}
        self.send({"type": "approval_request", "id": aid, "action": "organize_folder",
                   "summary": "Move 89 files in Downloads into folders: 32 University, 20 Documents, 18 Archives, "
                              "7 Installers, 7 Images, 3 Presentations, 1 Bills & Receipts, 1 Videos"})
        done.wait(130)
        if self.approvals[aid]["ok"]:
            time.sleep(1.0)
            self.reply("Moved 89 files. 32 University, 20 Documents, 18 Archives, 7 Installers, 7 Images, 3 Presentations, "
                       "1 Bills & Receipts, 1 Videos. Say 'undo' to put them back.", "local", "filemanager")
        else:
            self.reply("Okay, I left everything as it was.", "local", "filemanager")

    def coder(self):
        self.used += 1
        self.budget()
        self.ev("start", persona="coder", query="make a python file")
        time.sleep(0.9)
        self.tools(["write_code_file", "open_in_ide"], 1.1)
        md = ("I created **bubble_sort.py** in your *Nyro Code* folder and opened it in VS Code.\n\n"
              "```python\ndef bubble_sort(items):\n    items = list(items)\n    for i in range(len(items)):\n"
              "        for j in range(len(items) - i - 1):\n            if items[j] > items[j + 1]:\n"
              "                items[j], items[j + 1] = items[j + 1], items[j]\n    return items\n```\n\n"
              "- Runs in `O(nÂ²)`, fine for small lists\n- Add `if __name__ == \"__main__\":` to try it")
        self.stream(md)
        self.reply(md, "llm", "coder")

    def explain(self):
        self.used += 1
        self.budget()
        self.ev("start", persona="screencoach", query="explain this")
        time.sleep(0.8)
        self.tools(["capture_foreground_window"], 0.9)
        self.send({"type": "screenshot", "mime": "image/png", "b64": fake_screen()})
        self.tools(["render_screen_annotations"], 1.0)
        md = "That's the **subscribe** button for the channel. I've boxed it in red on your screen: press it to follow new videos."
        self.stream(md)
        self.reply(md, "llm", "screencoach")

    def act(self):
        self.used += 1
        self.budget()
        self.ev("start", persona="desktopctl", query="act")
        time.sleep(0.8)
        self.tools(["request_control", "capture_screen", "click", "type_text", "press_keys"], 0.8)
        md = "Done: I opened **Gemini** in Chrome and typed *hello* into the prompt box."
        self.stream(md)
        self.reply(md, "llm", "desktopctl")

    def on_msg(self, m):
        t = m.get("type")
        if t == "command":
            threading.Thread(target=self.handle, args=(m["text"],), daemon=True).start()
        elif t == "approval_response":
            a = self.approvals.get(int(m["id"]))
            if a:
                a["ok"] = bool(m.get("approved"))
                a["event"].set()
        elif t == "sync":
            self.budget()
            self.ev("usage", five_hour=0.34, status="allowed")
        elif t == "audio":
            self.send({"type": "transcript", "rid": m.get("rid"), "text": "open youtube in chrome", "lang": "en"})


def main():
    with connect(URL, open_timeout=10, max_size=None) as ws:
        ws.send(json.dumps({"type": "auth", "token": TOKEN}))
        print("auth:", ws.recv(timeout=5), flush=True)
        mock = Mock(ws)
        print("mock host online. Ctrl+C to stop.", flush=True)
        while True:
            try:
                msg = json.loads(ws.recv())
            except Exception as e:
                print("closed:", e)
                break
            mock.on_msg(msg)


if __name__ == "__main__":
    sys.exit(main())

