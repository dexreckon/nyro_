# Nyro host installer for Windows (PowerShell 5+). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\install_host.ps1
# Creates .venv, installs dependencies, stores the pairing token, and starts Nyro at login.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.10+ not found. Install from python.org and tick 'Add to PATH'." }
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-host.txt
& .\.venv\Scripts\python.exe -m playwright install chromium 2>$null   # optional: only WebPilot needs it

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  Write-Warning "The 'claude' CLI is not on PATH. Install Claude Code and run 'claude' once to log in with your Claude Pro account."
}

# Pairing token for the phone remote (stored for your user only; never written to the repo)
$tok = Read-Host "Pairing token from your VPS .env (NYRO_PAIR_TOKEN), or press Enter to skip the phone remote"
if ($tok) { [Environment]::SetEnvironmentVariable("NYRO_PAIR_TOKEN", $tok, "User") }
$url = Read-Host "Relay WebSocket URL, e.g. wss://nyro.example.com/ws/host (Enter to skip)"
if ($url) {
  $local = Join-Path $root "nyro_config.local.json"
  $cfg = if (Test-Path $local) { Get-Content $local -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
  $cfg | Add-Member -Force -NotePropertyName relay -NotePropertyValue ([pscustomobject]@{ url = $url })
  $cfg | ConvertTo-Json -Depth 6 | Set-Content $local -Encoding UTF8
}

# Start at login with no console window (pythonw), via a Startup-folder shortcut
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Nyro.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $root ".venv\Scripts\pythonw.exe"
$sc.Arguments = "-m host.app"
$sc.WorkingDirectory = $root
$sc.Save()
Write-Host "Installed. Nyro will start at your next login. Start it now with:  .venv\Scripts\pythonw.exe -m host.app"
