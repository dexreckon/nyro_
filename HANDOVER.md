# Nyro: Handover (start locally)

Nyro is a voice and chat assistant for your Windows laptop that you can also control from your phone.
This file is everything you need to run it yourself. Read sections 1 to 3 first.

```
Phone app / browser ──https──> Relay (small server) <──outbound── Windows laptop "host"
                                                                    ├─ voice (hotkey / "Hey Nyro") -> speech-to-text
                                                                    ├─ simple commands run locally (0 AI requests)
                                                                    ├─ everything else -> Claude (haiku, low effort, 4 scoped tool sets)
                                                                    └─ red cursor / highlights drawn over any app
```

## 0. What is in the zip
| Folder / file | What it is |
|---|---|
| `host/` | The Windows program (tray app, voice, Claude runner, overlay, tool servers, firewall) |
| `vps/` | The relay server (FastAPI) + Dockerfile + Caddy config |
| `pwa/` | The chat UI (phone web app, also used inside the Android app) |
| `mobile/` | Android project (Capacitor) that wraps `pwa/` into an APK |
| `scripts/` | `install_host.ps1` (Windows installer), `demo_overlay.py` |
| `docs/` | `SHARED_VPS.md` (run next to another app), `WAKEWORD.md` (train "Hey Nyro") |
| `tests/` | 163 Python tests, JS unit tests, a browser end-to-end test |
| `nyro_config.json` | All settings (don't edit; override in `nyro_config.local.json`) |

If your zip is the **APK artifact** (it only contains `nyro.apk`), you also need the repo zip:
https://github.com/dexreckon/nyro_/archive/refs/heads/claude/compassionate-fermat-7tw05n.zip
The newest APK is always at https://github.com/dexreckon/nyro_/releases/tag/nyro-android-latest

## 1. Install the prerequisites (Windows, once)
1. **Python 3.11 or 3.12** from python.org (tick "Add python.exe to PATH").
2. **Claude Code CLI**, logged in with your Claude Pro account: install it, then run `claude` once and sign in.
3. **Chrome** (only needed for the web-browsing feature).
4. Unzip the repo to a short path, e.g. `C:\nyro`.

Open PowerShell in that folder, then:
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-host.txt -r vps\requirements.txt
```
The first install is large (speech models, Qt). The speech model downloads (~0.5 GB) the first time you speak.

## 2. Ten-minute local trial (everything on this one PC, no server needed)
Make one secret and use it for both the relay and the host:
```powershell
$env:NYRO_PAIR_TOKEN = python -c "import secrets; print(secrets.token_hex(32))"
$env:NYRO_DB = "$PWD\relay.sqlite3"
```
**Window 1 (relay):**
```powershell
python -m uvicorn vps.relay:app --host 127.0.0.1 --port 8000
```
**Window 2 (the Nyro host).** The token variable is per-window, so set it again here with the same value:
```powershell
$env:NYRO_PAIR_TOKEN = "<paste the same token>"
.\.venv\Scripts\activate
python -m host.app
```
A red ring icon appears in the system tray. **Ctrl+Alt+N** starts listening. The relay window must stay open.

**Open the chat:** go to http://127.0.0.1:8000 in Chrome, paste the token, done. Try:
- "volume 30", "mute", "battery", "snap chrome left" (instant, no AI)
- "awaz kam karo" / "آواز کم کرو" (same, in Urdu)
- "help me here" with some app open (Claude looks at the window and points at what to click)

Tray menu: AI request counter, "Listen now", "+100 requests today", Quit. **Ctrl+Shift+X** clears drawings.

Overlay only, without Claude: `python -m host.overlay.screen_canvas` in one window, `python scripts\demo_overlay.py` in another.

## 3. The real setup (VPS + your phone)
1. **VPS:** follow the README section "VPS". If it already runs QuickBites or anything else, use `docs/SHARED_VPS.md`.
   You need a subdomain such as `nyro.yourdomain.com` and a token (`openssl rand -hex 32`).
2. **Laptop:** run `scripts\install_host.ps1`. It stores the token and relay URL (`wss://nyro.yourdomain.com/ws/host`)
   and makes Nyro start at login.
3. **Your folders (optional but needed for notes):** create `nyro_config.local.json` next to `nyro_config.json`:
   ```json
   { "firewall": { "allowed_roots": ["C:\\Users\\YOU\\Notes"] } }
   ```
   Without it the file tools stay disabled.
4. **Phone:** install `nyro.apk` (allow "install unknown apps" once). First screen: `https://nyro.yourdomain.com` + token.
   The APK only accepts HTTPS. Emulator: drag the APK onto the window, and turn on the host microphone in its settings.
5. **"Hey Nyro" (optional):** see `docs/WAKEWORD.md`. Until you add `models/hey_nyro.onnx`, use Ctrl+Alt+N.
6. **Browser control (optional):** start Chrome with a separate profile so it accepts remote control:
   `chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\nyro-chrome` (recent Chrome ignores the flag on your normal profile),
   Nyro attaches to that Chrome (the extra Playwright browser download is not required).

## 4. How it behaves
- **Costs:** simple commands never call Claude. Anything else is 1 request. Limit: **400 AI requests per day**
  (`budget.daily_request_cap`). At the limit only AI features pause; local commands keep working. Failed calls are refunded.
- **Safety tiers:** green runs by itself (drawing, brightness, volume, reading allowed notes). Yellow asks **your phone**
  to Approve/Reject for 2 minutes (writing/deleting notes, clicking Submit/Pay/Send in the browser). Red is always blocked
  (System32, registry, shell commands, killing system processes). Passwords/card fields are never typed by Nyro.
- **Claude has no Bash/Read/Write.** It only gets the tools of the matching persona. Tested against a prompt asking it to run shell commands: refused.
- **Deleting** moves a file to `.nyro_trash` inside your folder. Nothing is permanently deleted.

## 5. Settings you may want (put them in `nyro_config.local.json`)
| Setting | Default | Meaning |
|---|---|---|
| `budget.daily_request_cap` | 400 | AI requests per day |
| `wake_word.hotkey` | `ctrl+alt+n` | Listen hotkey |
| `wake_word.threshold` | 0.5 | Raise if "Hey Nyro" false-triggers |
| `voice.stt_model` | `small` | `base` is faster, `medium` is more accurate for Urdu |
| `voice.voices` | Aria / Uzma | edge-tts voices (English / Urdu) |
| `relay.url` | `ws://127.0.0.1:8000/ws/host` | Where the host connects |
| `firewall.allowed_roots` | `[]` | Folders Nyro may read/write |

## 6. Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| Relay says `NYRO_PAIR_TOKEN must be set` | Token must be 24+ characters and set in that window |
| Chat shows "Laptop offline" | Host not running, or its token/relay URL differs from the relay's |
| "Too many attempts" | 5 wrong tokens locks that IP for 5 minutes |
| Tray icon but nothing happens on Ctrl+Alt+N | Run `python -m host.app` in a console and read the log lines starting with `[wake]`, `[tts]`, `[relay]` |
| "claude CLI not found" | Install Claude Code; make sure `claude` works in the same window |
| Red cursor in the wrong place | Display scaling. Tell Claude your Windows scale % and monitor setup |
| No voice reply | Needs internet (edge-tts). Check the `[tts]` log line |
| APK says can't reach server | Must be `https://`, DNS pointing at the VPS, relay healthy at `https://your-domain/healthz` |

## 7. What is verified and what is NOT
**Verified by automated tests (163 Python + 13 JS + a real-browser run):** firewall and path jail, request budget, local
command router (English/Urdu/Roman Urdu), Claude runner (including one real `claude -p` call with a real tool server),
all four tool servers over MCP, approval flow, relay security (auth, lockout, size/rate limits, approval expiry),
the chat UI in mobile + desktop + a simulated Android shell, and the APK build in GitHub Actions.

**Never run on a real Windows machine yet (expect small fixes):** volume/brightness/window control, screen capture,
the click-through overlay and its alignment on high-DPI screens, the tray icon, microphone recording + Whisper,
text-to-speech playback, the Windows installer script, Chrome control, and voice dictation inside the Android app.
**First thing to do:** run section 2, then tell Claude exactly what worked and what did not.

## 8. Not built yet
QR-code pairing, "Hey Nyro" model (needs training), Play Store signing, a stop button for a running request,
multi-user support. The APK uses a shared sideload key (fine for you, not for publishing).

## 9. Continue with Claude
Open the repo in Claude Code and say, for example: "Read HANDOVER.md. I ran section 2 and [what happened]." Paste the first
error line and the log lines around it. The code is on branch `claude/compassionate-fermat-7tw05n`
(https://github.com/dexreckon/nyro_). Pushing to `mobile/` or `pwa/` rebuilds the APK automatically.
