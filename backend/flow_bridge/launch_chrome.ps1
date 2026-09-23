$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

$Chrome = if ($env:TH_MEDIA_CHROME_PATH) {
  [Environment]::ExpandEnvironmentVariables($env:TH_MEDIA_CHROME_PATH)
} elseif (Test-Path 'C:\Program Files\Google\Chrome\Application\chrome.exe') {
  'C:\Program Files\Google\Chrome\Application\chrome.exe'
} else {
  'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'
}

$Data = if ($env:TH_MEDIA_DATA_DIR) {
  [Environment]::ExpandEnvironmentVariables($env:TH_MEDIA_DATA_DIR)
} else {
  Join-Path $Root '.data'
}

$Profile = if ($env:TH_MEDIA_FLOW_PROFILE_DIR) {
  [Environment]::ExpandEnvironmentVariables($env:TH_MEDIA_FLOW_PROFILE_DIR)
} else {
  Join-Path $Data 'flow_chrome_profile'
}

$CdpUrl = if ($env:FLOW_CDP_URL) { $env:FLOW_CDP_URL } else { 'http://127.0.0.1:9223' }
$CdpUri = [Uri]$CdpUrl
$CdpPort = $CdpUri.Port

if (-not (Test-Path $Chrome)) { throw 'Google Chrome not found.' }
if (Get-NetTCPConnection -State Listen -LocalPort $CdpPort -ErrorAction SilentlyContinue) {
  throw "Port $CdpPort is already in use."
}

New-Item -ItemType Directory -Force -Path $Profile | Out-Null
Start-Process -FilePath $Chrome -ArgumentList @(
  "--remote-debugging-port=$CdpPort",
  "--user-data-dir=$Profile",
  '--restore-last-session',
  '--start-minimized',
  '--no-first-run',
  '--no-default-browser-check'
)
Write-Output "Flow Chrome started with profile: $Profile on CDP port $CdpPort"
