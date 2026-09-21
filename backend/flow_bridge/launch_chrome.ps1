$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$Profile = Join-Path $Root '.data\flow_chrome_profile'
if (-not (Test-Path $Chrome)) { throw 'Google Chrome not found.' }
if (Get-NetTCPConnection -State Listen -LocalPort 9223 -ErrorAction SilentlyContinue) { throw 'Port 9223 is already in use.' }
New-Item -ItemType Directory -Force -Path $Profile | Out-Null
Start-Process -FilePath $Chrome -ArgumentList @('--remote-debugging-port=9223', "--user-data-dir=$Profile", '--restore-last-session', '--start-minimized', '--no-first-run', '--no-default-browser-check')
Write-Output "Flow Chrome started with profile: $Profile"
