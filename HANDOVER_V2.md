# Nyro: Handover v2 (what was built after the first Windows run)

`HANDOVER.md` is the original start-up guide. This file is everything that happened after it: the Windows fixes, the new
features, how they are protected, how to run and test them, and what is still unverified. Read section 1 first.

Branch: `claude/compassionate-fermat-7tw05n` (https://github.com/dexreckon/nyro_). History: `906bddd` original handover,
`1538942` first Windows round, `e29f358` file manager / coder / interruptible voice, then the mobile UI redesign (this commit).

## 1. Where things stand

| Area | State |
|---|---|
| Relay + host on one Windows PC, app window, tray, Ctrl+Alt+N hotkey | Works (tested live by the user) |
| Instant (zero-AI) commands: volume, mute, battery, open site/app, close tab, stop, file manager | Works (tested live) |
| Voice replies (edge-tts) | Fixed (was distorted), confirmed clean by the user |
| Dynamic Island pill with live mic waveform | Works (confirmed by the user) |
| Phone access through a Cloudflare quick tunnel | Tunnel verified from outside; phone app itself not confirmed |
| Phone voice (record on phone, laptop Whisper transcribes) | Built, PC side verified with a real webm/opus sample; **not tried on a real phone** |
| Assignment PDF to Word solution | Built and used once by the user; reply said it worked |
| Mouse/keyboard control (DesktopCtl) | Built, logic tested with a fake device; **not run live end to end** |
| Voice barge-in (talk over Nyro to stop it) | Built, detector unit-tested; **not tested with a real microphone** |
| File manager: organise Downloads/Desktop, desktop icon arranger, search index | Downloads organised for real (89 files, undoable); icon arrange + restore verified on the real desktop; search verified |
| Coder mode (write .py/.cpp/.js/.dart ... and open in an IDE) | Live-tested file write + VS Code launch; the Claude-driven flow was used by the user |
| Mobile UI redesign (`pwa/`) | Verified at phone size in dark and light with a stand-in host; **not run inside the APK yet** (needs the rebuild this push triggers) |
| Tests | 233 Python passed, 15 skipped; 13 UI (node) tests passed |

Not built / not verified: "Hey Nyro" wake word (needs a trained model, see `docs/WAKEWORD.md`), Play Store signing, a stop
button for a running Claude request, multi-user, a permanent public address (the tunnel is temporary).

## 2. Architecture (unchanged shape, more inside)

```
Phone app / browser --https--> Relay (FastAPI) <--outbound-- Windows host
                                                  |- hotkey / barge-in -> Whisper (medium) -> router
                                                  |- instant commands run locally (0 AI requests)
                                                  |- everything else -> Claude (haiku, low effort) with ONE persona's tool set
                                                  |- Dynamic Island + red-cursor overlay (separate Qt processes)
                                                  |- search index (SQLite) refreshed in the background
```

Personas (each = one MCP server with its own scoped tools; Claude has no Bash/Read/Write):
`screencoach` (explain + draw), `sysop` (volume, windows, open site/app, close tab), `webpilot` (kept, no longer routed),
`fileclerk` (read PDF/Word, write Word), `desktopctl` (mouse + keyboard), `coder`, `filemanager`.

**Routing rule (`host/intent.py`):** anything that is not a question or an "explain" goes to `desktopctl` (act, don't
point). `screencoach` is used for "explain / where is / how do I" questions. Documents/assignments, code files and
file-manager phrases have their own personas. Simple commands never reach Claude.

## 3. Run it

Windows, Python 3.11, Claude Code CLI signed in (`claude auth login`; the CLI uses the Claude account, no API key).
Scripts are blocked by default execution policy, so use the `.bat`.

```
C:\nyro\start_nyro.bat        # starts the relay (if :8000 is free), the host, the app window; prints the pairing token
```

- The pairing token is stored in `C:\nyro\.token` (git-ignored). Never commit it.
- Run Nyro from a normal terminal in the **user's desktop session**. Started from a background/service shell it has no
  tray icon and the global hotkey does nothing (this cost an hour once).
- Phone: needs an `https://` address. Temporary: `cloudflared tunnel --url http://127.0.0.1:8000` (Cloudflare quick
  tunnel; address changes on restart). Permanent: the VPS route in `HANDOVER.md` section 3.
- APK: https://github.com/dexreckon/nyro_/releases/download/nyro-android-latest/nyro.apk (rebuilt by GitHub Actions whenever
  `mobile/` or `pwa/` change).

## 4. What it can do now

**Instant commands (no AI request)** - English, Roman Urdu/Hindi, Urdu script, Devanagari:
volume/brightness/mute/lock/battery; `open youtube in chrome`, `open vs code`, `open windows update`, `play X on youtube`;
`close youtube tab` (Ctrl+W on a real browser window, cycles tabs to find it); `snap chrome left`; `stop` / `ruk jao` (silences
voice and revokes mouse/keyboard control); `organize my downloads`, `tidy my desktop`, `arrange desktop icons`,
`restore desktop layout`, `undo`, `open downloads`, `find my assignment pdf` (opens Explorer on the best match).

**Voice (`host/voice/`)**
- Hotkey Ctrl+Alt+N stops any speech and listens. Whisper model is `medium` (set in `nyro_config.local.json`).
- `tts.py`: playback decodes to one fixed device format and feeds exact frame counts (fixed the distorted voice); `stop()`
  silences within ~50 ms; a new utterance cuts the previous one.
- `bargein.py`: while Nyro talks it listens; it learns how loud Nyro's own voice is in the mic and only triggers when the user
  is clearly louder for ~300 ms, then stops speech and records (keeping 0.55 s of pre-roll). `voice.barge_in` turns it off.
- Dynamic Island (`overlay/island.py`, UDP :8767): listening (waveform), thinking, done, error. Mouse/keyboard control also
  shows "Controlling your mouse and keyboard - Ctrl+Shift+Q stops".

**File manager** (`organizer.py`, `fileindex.py`, `desktop_icons.py`, `filemanager.py`, `mcp_servers/filemanager_mcp.py`)
- Organise only Downloads and Desktop (config allow-list). Moves files into category folders (Documents, Images, Videos,
  Archives, Installers, Code, Presentations, Spreadsheets, University, Bills & Receipts, Other). Never deletes or overwrites;
  skips folders, shortcuts on Desktop, in-progress/hidden/just-modified files; asks for approval with exact counts; every run is
  journaled in a hidden `.nyro_moves.json` inside the folder so `undo` restores it. Tampered journals cannot move files out of the folder.
- Desktop icons: talks to Explorer's desktop list view; changes positions only; saves the previous layout; restore verified exact.
- Search: SQLite FTS5 index (`nyro_data/index.sqlite3`) of names + first ~2000 chars of PDF/Word/text in Downloads, Desktop, Documents;
  refreshed in the background; moves update it.

**Coder** (`mcp_servers/coder_*.py`, `ide.py`): writes complete source files into `~/Documents/Nyro Code` and opens them in the
named IDE (VS Code default; Cursor, Antigravity, Android Studio, PyCharm, IntelliJ, CLion, WebStorm, Sublime, Notepad++,
Visual Studio). Blocks `.bat/.ps1/.exe/...`. Overwriting an existing file needs approval. It cannot run code.

**Documents** (`fileclerk_*`): reads PDF/Word, writes `.docx` to `Downloads\Nyro Solutions\`, opens the result; slow jobs
send a Windows notification.

**Mouse/keyboard** (`desktopctl_*.py`, `mcp_servers/desktopctl_mcp.py`): own grant file (5 min / 150 actions, auto-approved by
default, `desktopctl.auto_approve`), kill switches Ctrl+Shift+Q and the top-left screen corner, refuses terminals, Task Manager,
registry, UAC, Settings and sign-in windows, card-like text, Win key, Alt+F4. Also has `open_target` and `close_tab`.

**Phone voice**: the app records with MediaRecorder, sends `{type:"audio"}` (<=400 KB, rate-limited) through the relay, the host
transcribes with the same Whisper model (`Transcriber.transcribe_bytes`) and returns `{type:"transcript"}`; the phone then sends it as a
normal command. Transcripts are never kept in the relay's replay history.

**Mobile UI (this commit)**: redesigned `pwa/` (dark-first glass UI, brand gradient, live status pill, feature cards, steps with
check marks, code panels, Undo chips, recording waveform bar, bottom-sheet approvals, friendlier tool labels in `nyro-lib.js`).
`sw.js` cache is `nyro-shell-v4`. `scripts/mock_host.py` plays realistic conversations for UI work without the real host.

## 5. Safety model (enforced in code)

Tiers in `host/firewall.py`: green runs, yellow asks the phone/app (write/overwrite files, organise folders, mouse/keyboard if
`auto_approve` is off), red is blocked. Notable guards: path jail per tool, protected system paths, passwords/cards never typed,
file contents are treated as untrusted data in every persona prompt, the relay lock-out/size/rate limits, whitelisted sites/apps/IDEs
only (`open_target`, `ide.py`), blocked file types for code writes, journaled undo for every file move.

Approvals can be declined or time out (120 s); a declined approval is reported to the user, not retried.

## 6. Configuration (`nyro_config.json`; override in `nyro_config.local.json`, git-ignored)

New keys: `persona_overrides` (timeouts/max turns per persona; all personas use `claude-haiku-5-5` + `low` effort to save
plan usage), `island`, `desktopctl.auto_approve`, `code.workspace` / `code.default_ide`, `organizer.roots` /
`min_age_seconds` / `extra_rules`, `index.roots` / `refresh_minutes`, `voice.barge_in`, `voice.stt_model`.
The local file on the user's PC sets `voice.stt_model: medium` and `firewall.allowed_roots` to their Downloads folder. Do not commit it.

## 7. Windows problems found and fixed

1. `pycaw` newer API: `AudioDevice` has no `Activate` -> use `.EndpointVolume`.
2. npm's `claude.cmd` shim can't be started by `subprocess` without a shell -> `resolve_cli()` finds the real `claude.exe`.
3. Distorted TTS: format mismatch + wrong chunk sizes -> rewritten playback.
4. Screen capture grabbed the Nyro window itself -> skips windows titled "Nyro".
5. Hotkey/tray dead when launched from a background shell (see section 3).
6. Speech-to-text adds a trailing "." -> intent parsing strips it everywhere.
7. Python `\w` drops Devanagari vowel signs -> `normalize()` keeps them.
8. `open X` swallowed longer instructions -> it only fires for plain "open X (in Y)".
9. Zip extraction in a deep folder fails on Windows (260-char path) -> use a short path such as `C:\nyro`.
10. PowerShell script execution is disabled -> `start_nyro.bat` runs the script with `-ExecutionPolicy Bypass` for that process only.

## 8. Tests

`python -m pytest tests -q` -> 233 passed, 15 skipped (browser e2e needs Playwright). `node --test tests/js/lib.test.js` -> 13 passed
(the CI build runs exactly this). New suites: `test_open_intent`, `test_docs_and_control`, `test_island`, `test_phone_voice`,
`test_coder_and_interrupt`, `test_file_manager`. Live check scripts (not in the repo, in `C:\nyro\tools`): close-tab,
desktop arrange/restore, file preview/organise, phone-voice transcription, stop-speech timing.

## 9. Things that live outside the repo

- `C:\nyro\.token`, `C:\nyro\nyro_config.local.json`, `C:\nyro\nyro_data\` (budget, index, journals, desktop layout backups), `C:\nyro\tools\`
  (cloudflared.exe, live-check scripts). All git-ignored.
- The user's Downloads folder was organised for real (89 files into 8 folders); `undo` reverses it via `.nyro_moves.json`.
- `C:\nyro_showcase\`: a separate Stageshow project with an animated product showcase of the new UI
  (`examples/nyro-app`, notes in its `REHEARSAL.md`, stills in `shots/`). Not part of this repo.
- GitHub clone used for pushing: `C:\nyro_push` (files are copied from `C:\nyro`, line endings normalised to LF; never copy the
  zip's Java/gradle files over the repo copy).

## 10. Next steps, in order

1. Install the rebuilt APK and test phone voice for real (mic permission, Urdu, laptop transcription speed).
2. Try mouse/keyboard control and voice barge-in on the real PC; tune `bargein.py` thresholds if it cuts itself off or never reacts.
3. Replace the quick tunnel with the VPS setup for a stable address; keep the token private (anyone with address + token controls Nyro).
4. Train the "Hey Nyro" model, then add a start-at-login shortcut for `start_nyro.bat`.
5. Add a stop button for running requests, per-folder rules for the organiser (config `organizer.extra_rules`), dark-theme showcase.

## 11. Troubleshooting additions

| Symptom | Fix |
|---|---|
| Nyro says "claude CLI not found" | Fixed in code; if it returns, run `where.exe claude` and check `%APPDATA%\npm` |
| "Not logged in" on Claude replies | Run `claude auth login`, then `claude auth status` -> `loggedIn: true` |
| Hotkey does nothing / no tray icon | Start from `start_nyro.bat` in your own terminal, not a background shell |
| Volume commands error | Update pycaw is not needed; the code handles old and new versions |
| UI looks old after an update | Hard-reload; the service worker cache is `nyro-shell-v4` |
| Dev relay already on :8000 when starting Nyro | Stop it (`vps.relay`), otherwise `start_nyro.bat` reuses it (and a mock host would answer) |
| Organise says "I can only organise: ..." | Folder is not in `organizer.roots` |
