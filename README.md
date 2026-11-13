# Nyro

A Windows desktop assistant you can talk to ("Hey Nyro", hotkey, or from your phone). It controls the PC,
coaches you through any app with an on-screen red cursor and highlights, drives your browser, and manages
notes, powered by **Claude Code CLI** on a Claude Pro login, pinned to `claude-haiku-5-5`.

```
 Phone PWA ──wss──> VPS relay (FastAPI + Caddy) <──wss── Windows host (tray app)
                                                          ├─ wake word / hotkey -> faster-whisper (en/ur)
                                                          ├─ local intent router  (0 AI requests)
                                                          ├─ claude -p  (haiku, low effort, scoped MCP personas)
                                                          └─ click-through overlay (PyQt6) + edge-tts voice
```

## What costs AI requests (and what doesn't)
| Command | Cost |
|---|---|
| volume / brightness / mute / lock / window snap / battery / clear overlay (English, Urdu, Roman Urdu) | **0**: handled locally |
| "guide me", "explain this slide", browser tasks, notes | **1 request** per task (a whole multi-step guide is one plan) |

Daily cap: **400** AI requests (`budget.daily_request_cap`). At the cap only AI features pause; everything
local keeps working. Tray menu: "Allow +100 requests today". Failed calls are refunded.

## Safety model
- Claude runs with **no built-in tools** (`--tools ""`): no Bash, Write, Read. Each persona only gets its own MCP tools.
- Firewall tiers: **green** auto-runs, **yellow** (write/delete files, Submit/Pay/Send clicks) needs your phone's
  Approve, **red** (System32, registry, shell, critical processes) is always blocked.
- File tools are jailed to `firewall.allowed_roots`; deletes go to `.nyro_trash`.
- The relay forwards only whitelisted message types, checks a long pairing token in constant time, locks out
  brute force, expires approvals, and serves the PWA under a strict CSP.

## Setup

### 1. VPS (4 GB is plenty)
Already running other apps (e.g. QuickBites)? See **[docs/SHARED_VPS.md](docs/SHARED_VPS.md)**: Nyro adds one
small container and does not touch ports 80/443 by default.
```bash
cp vps/.env.example .env      # set NYRO_PAIR_TOKEN (openssl rand -hex 32)
docker compose up -d --build                    # relay on 127.0.0.1:8000; put your proxy in front
# dedicated VPS with nothing on 80/443:  docker compose --profile caddy up -d --build   (also set NYRO_DOMAIN)
```
Open `https://<your nyro domain>` on your phone, paste the token, and "Add to Home screen".

### 2. Windows laptop
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_host.ps1
```
Then create `nyro_config.local.json` (git-ignored) with your folders:
```json
{ "firewall": { "allowed_roots": ["C:\\Users\\you\\Notes"] } }
```
Log in to Claude Code once (`claude`) with your Pro account. Start Nyro: `.venv\Scripts\pythonw.exe -m host.app`.

### 3. Wake word
See [docs/WAKEWORD.md](docs/WAKEWORD.md). Until the model exists, use `Ctrl+Alt+N`.

### Try the overlay alone
```powershell
.venv\Scripts\python -m host.overlay.screen_canvas     # terminal 1
.venv\Scripts\python scripts\demo_overlay.py           # terminal 2
```
`Ctrl+Shift+X` clears the screen.

## Tests
```bash
pip install -r requirements-host.txt fastapi uvicorn
python -m unittest discover -s tests -t .
```
The browser E2E (`tests/test_pwa_e2e.py`) needs Node + Playwright and is skipped otherwise.

## Known limits
- Pointing accuracy at 800x450 is good for large/medium targets; very small icons may be off by a few pixels.
- Multi-step guides assume the layout stays the same between steps; a step that changes the layout should set
  `recapture_after`, which costs one extra request to re-plan.
- Windows-only pieces (volume, brightness, window control, capture, overlay click-through, tray) are covered by
  unit tests with fakes; they need a real Windows run to confirm.
