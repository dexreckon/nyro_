$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$tokenFile = Join-Path $PSScriptRoot '.token'
if (-not (Test-Path $tokenFile)) {
    & $py -c "import secrets; print(secrets.token_hex(32))" | Set-Content -NoNewline $tokenFile
}
$env:NYRO_PAIR_TOKEN = (Get-Content $tokenFile -Raw).Trim()
$env:NYRO_DB = Join-Path $PSScriptRoot 'relay.sqlite3'
$env:PYTHONUNBUFFERED = '1'

# relay: start only if nothing answers on :8000
$up = $false
try { Invoke-WebRequest http://127.0.0.1:8000/healthz -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true } catch {}
if (-not $up) {
    Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList '-m','uvicorn','vps.relay:app','--host','127.0.0.1','--port','8000'
    Start-Sleep -Seconds 3
}
Write-Host "Nyro starting. Pairing token (paste once into the app window): $env:NYRO_PAIR_TOKEN"
& $py -m host.app
