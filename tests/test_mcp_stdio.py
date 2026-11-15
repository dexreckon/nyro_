"""Spawn real MCP servers over stdio (as Claude Code does) and call tools."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from host.approval import ApprovalBroker

ROOT = Path(__file__).resolve().parent.parent

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAVE_MCP = True
except Exception:  # pragma: no cover
    HAVE_MCP = False


async def call(module, env, tool, args=None, list_only=False):
    params = StdioServerParameters(command=sys.executable, args=["-m", module], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            if list_only:
                return sorted(t.name for t in (await s.list_tools()).tools)
            res = await s.call_tool(tool, args or {})
            return res.content[0].text


@unittest.skipUnless(HAVE_MCP, "mcp SDK missing")
class StdioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "notes"
        self.root.mkdir()
        (self.root / "a.md").write_text("alpha")
        cfg = json.loads((ROOT / "nyro_config.json").read_text())
        cfg["firewall"]["allowed_roots"] = [str(self.root)]
        cfg["data_dir"] = str(Path(self.tmp.name) / "data")
        self.cfg_path = Path(self.tmp.name) / "cfg.json"
        self.cfg_path.write_text(json.dumps(cfg))
        self.decisions = []
        self.broker = ApprovalBroker(lambda p: self.decisions.append(p) or self.broker.resolve(p.id, self.answer), port=0)
        self.broker.start()
        self.answer = True
        self.env = {**os.environ, "NYRO_CONFIG": str(self.cfg_path), "PYTHONPATH": str(ROOT),
                    "NYRO_APPROVAL_URL": f"http://127.0.0.1:{self.broker.port}/approve"}

    def tearDown(self):
        self.broker.stop()
        self.tmp.cleanup()

    def run_tool(self, mod, tool, args=None):
        return asyncio.run(call(mod, self.env, tool, args))

    def test_tool_lists(self):
        want = {
            "host.mcp_servers.fileclerk_mcp": ["list_allowed_files", "open_path", "read_document", "soft_delete_file",
                                                  "write_docx", "write_markdown"],
            "host.mcp_servers.filemanager_mcp": ["arrange_desktop_icons", "open_folder", "open_found_file",
                                                 "organize_folder", "preview_organize", "reindex_files",
                                                 "restore_desktop_layout", "search_files", "show_in_explorer",
                                                 "undo_organize"],
            "host.mcp_servers.coder_mcp": ["installed_ides", "list_code_files", "open_in_ide", "read_code_file",
                                           "write_code_file"],
            "host.mcp_servers.desktopctl_mcp": ["capture_screen", "click", "close_tab", "open_target", "press_keys",
                                                "release_control", "request_control", "scroll", "type_text"],
            "host.mcp_servers.sysop_mcp": ["close_tab", "get_system_telemetry", "lock_workstation", "manage_window",
                                           "open_target", "set_brightness", "set_volume", "toggle_mute"],
            "host.mcp_servers.screencoach_mcp": ["capture_foreground_window", "clear_annotations", "render_screen_annotations"],
            "host.mcp_servers.webpilot_mcp": ["capture_tab_screenshot", "click_element", "extract_dom_text", "open_url", "type_text"],
        }
        for mod, tools in want.items():
            self.assertEqual(asyncio.run(call(mod, self.env, None, list_only=True)), tools, mod)

    def test_fileclerk_green_read(self):
        fc = "host.mcp_servers.fileclerk_mcp"
        self.assertEqual(self.run_tool(fc, "list_allowed_files"), "a.md")
        self.assertEqual(self.run_tool(fc, "read_document", {"rel_path": "a.md"}), "alpha")
        self.assertEqual(self.decisions, [])  # green: no approval asked

    def test_fileclerk_jail(self):
        out = self.run_tool("host.mcp_servers.fileclerk_mcp", "read_document", {"rel_path": "../../etc/passwd"})
        self.assertTrue(out.startswith("DENIED") or out.startswith("ERROR"), out)

    def test_write_needs_and_gets_approval(self):
        out = self.run_tool("host.mcp_servers.fileclerk_mcp", "write_markdown", {"rel_path": "b.md", "content": "# b"})
        self.assertEqual(out, "Saved b.md")
        self.assertEqual((self.root / "b.md").read_text(), "# b")
        self.assertEqual([d.action for d in self.decisions], ["write_markdown"])

    def test_write_rejected_on_phone(self):
        self.answer = False
        out = self.run_tool("host.mcp_servers.fileclerk_mcp", "write_markdown", {"rel_path": "c.md", "content": "x"})
        self.assertTrue(out.startswith("DENIED"), out)
        self.assertFalse((self.root / "c.md").exists())

    def test_soft_delete_after_approval(self):
        out = self.run_tool("host.mcp_servers.fileclerk_mcp", "soft_delete_file", {"rel_path": "a.md"})
        self.assertTrue(out.startswith("Moved to .nyro_trash/"), out)
        self.assertFalse((self.root / "a.md").exists())

    def test_no_broker_means_denied(self):
        self.env["NYRO_APPROVAL_URL"] = "http://127.0.0.1:9/approve"
        out = self.run_tool("host.mcp_servers.fileclerk_mcp", "write_markdown", {"rel_path": "d.md", "content": "x"})
        self.assertTrue(out.startswith("DENIED"), out)

    def test_sysop_telemetry(self):
        out = json.loads(self.run_tool("host.mcp_servers.sysop_mcp", "get_system_telemetry"))
        self.assertIn("cpu_percent", out)

    def test_sysop_red_block(self):
        out = self.run_tool("host.mcp_servers.sysop_mcp", "manage_window", {"app_name": "reg add HKLM\\x", "action": "focus"})
        self.assertTrue(out.startswith("DENIED"), out)

    def test_screencoach_render_without_capture(self):
        out = self.run_tool("host.mcp_servers.screencoach_mcp", "render_screen_annotations",
                            {"annotations_json": "[]"})
        self.assertTrue(out.startswith("ERROR"), out)


if __name__ == "__main__":
    unittest.main()
