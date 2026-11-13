import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_relay import TOKEN, Server

ROOT = Path(__file__).resolve().parent.parent


def _have_playwright() -> bool:
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=str(ROOT))
    return r.returncode == 0


@unittest.skipUnless(_have_playwright(), "node + playwright not available")
class PwaE2E(unittest.TestCase):
    def test_pwa_against_real_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            from vps.relay import Settings, create_app
            import threading, time, uvicorn
            app = create_app(Settings(TOKEN, str(Path(tmp) / "r.db"), pwa_dir=str(ROOT / "pwa")))
            srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", ws_ping_interval=None))
            t = threading.Thread(target=srv.run, daemon=True); t.start()
            for _ in range(200):
                if srv.started:
                    break
                time.sleep(0.02)
            port = srv.servers[0].sockets[0].getsockname()[1]
            try:
                shots = ROOT / "tests/e2e/shots"
                shots.mkdir(exist_ok=True)
                r = subprocess.run(["node", str(ROOT / "tests/e2e/pwa_e2e.js"), str(port), TOKEN, str(ROOT / "pwa"), str(shots)],
                                   capture_output=True, text=True, timeout=150, cwd=str(ROOT), env=os.environ)
            finally:
                srv.should_exit = True; t.join(5)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PWA E2E OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
