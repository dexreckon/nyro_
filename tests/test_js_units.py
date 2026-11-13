import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "node not available")
class JsUnitTests(unittest.TestCase):
    def test_node_unit_tests(self):
        r = subprocess.run(["node", "--test", str(ROOT / "tests/js/lib.test.js")], capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout[-2500:] + r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
