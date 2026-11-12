"""Stand-in for the `claude` CLI: prints canned stream-json. Behaviour set via FAKE_CLAUDE env."""
import json
import os
import sys

mode = os.environ.get("FAKE_CLAUDE", "ok")
prompt = sys.stdin.read()
args = sys.argv[1:]
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"args": args, "prompt": prompt}) + "\n")

def out(o):
    print(json.dumps(o), flush=True)

if mode == "missing_exit":
    sys.stderr.write("auth failed")
    sys.exit(2)
if mode == "hang":
    import time
    time.sleep(60)
sid = "sess-" + str(abs(hash(prompt)) % 1000)
out({"type": "system", "subtype": "init", "session_id": sid})
if mode == "tool":
    out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__nyro-screencoach__capture_foreground_window"}]}})
out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}}})
out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}}})
out({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.31, "status": "allowed"}}}})
if mode == "error_result":
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom", "session_id": sid})
else:
    out({"type": "result", "subtype": "success", "is_error": False, "result": "Hello", "session_id": sid, "total_cost_usd": 0.0002})
