# Removes the login shortcut and the stored pairing token. Leaves the repo and .venv alone.
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "Nyro.lnk"
if (Test-Path $lnk) { Remove-Item $lnk }
[Environment]::SetEnvironmentVariable("NYRO_PAIR_TOKEN", $null, "User")
Write-Host "Nyro autostart and token removed."
