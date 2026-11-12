import json
import tempfile
import unittest
from pathlib import Path

from host.config import deep_merge, load_config


class ConfigTests(unittest.TestCase):
    def test_deep_merge(self):
        base = {"a": 1, "n": {"x": 1, "y": 2, "l": [1]}}
        self.assertEqual(deep_merge(base, {"n": {"y": 9, "l": [2]}, "b": 3}), {"a": 1, "n": {"x": 1, "y": 9, "l": [2]}, "b": 3})
        self.assertEqual(base["n"]["y"], 2)  # not mutated

    def test_local_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nyro_config.json"
            p.write_text(json.dumps({"budget": {"daily_request_cap": 400, "warn_ratio": 0.8}, "firewall": {"allowed_roots": []}}))
            self.assertEqual(load_config(p)["budget"]["daily_request_cap"], 400)
            (Path(d) / "nyro_config.local.json").write_text(
                json.dumps({"budget": {"daily_request_cap": 250}, "firewall": {"allowed_roots": ["C:\\Notes"]}}))
            cfg = load_config(p)
            self.assertEqual(cfg["budget"], {"daily_request_cap": 250, "warn_ratio": 0.8})
            self.assertEqual(cfg["firewall"]["allowed_roots"], ["C:\\Notes"])

    def test_repo_default_cap(self):
        self.assertEqual(load_config()["budget"]["daily_request_cap"], 400)


if __name__ == "__main__":
    unittest.main()
