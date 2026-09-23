$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root

function Wait-Port([int]$Port, [int]$Seconds = 20) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { return $true }
    Start-Sleep -Milliseconds 500
  }
  return $false
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
$CdpPort = ([Uri]$CdpUrl).Port
$BridgePort = if ($env:TH_MEDIA_FLOW_BRIDGE_PORT) { [int]$env:TH_MEDIA_FLOW_BRIDGE_PORT } else { 8765 }
$BridgeHost = if ($env:TH_MEDIA_FLOW_BRIDGE_HOST) { $env:TH_MEDIA_FLOW_BRIDGE_HOST } else { '127.0.0.1' }

python backend\flow_bridge\bootstrap.py | Out-Host

$cdpListener = Get-NetTCPConnection -State Listen -LocalPort $CdpPort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cdpListener) {
  $cdpProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($cdpListener.OwningProcess)" -ErrorAction SilentlyContinue
  $profileMarker = [Regex]::Escape($Profile)
  if (-not $cdpProcess -or $cdpProcess.CommandLine -notmatch $profileMarker) {
    throw "CDP port $CdpPort is occupied by another process. Runtime will not stop or replace it."
  }
}

$bridgeListener = Get-NetTCPConnection -State Listen -LocalPort $BridgePort -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bridgeListener) {
  $bridgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($bridgeListener.OwningProcess)" -ErrorAction SilentlyContinue
  if (-not $bridgeProcess -or $bridgeProcess.CommandLine -notlike '*uvicorn*flow_bridge.app:app*') {
    throw "Flow Bridge port $BridgePort is occupied by another process. Runtime will not stop or replace it."
  }
}

if (-not $cdpListener) {
  & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'launch_chrome.ps1') | Out-Host
  if (-not (Wait-Port $CdpPort 20)) { throw "Flow Chrome CDP failed to start on 127.0.0.1:$CdpPort." }
} else {
  Write-Output "Flow Chrome CDP already listening on $CdpPort."
}

if (-not (Get-NetTCPConnection -State Listen -LocalPort $BridgePort -ErrorAction SilentlyContinue)) {
  $bridgeScript = Join-Path $PSScriptRoot 'run_bridge.ps1'
  Start-Process powershell -WindowStyle Hidden -ArgumentList @('-ExecutionPolicy','Bypass','-File',('"' + $bridgeScript + '"'))
  if (-not (Wait-Port $BridgePort 20)) { throw "Flow Bridge failed to start on $($BridgeHost):$BridgePort." }
} else {
  Write-Output "Flow Bridge already listening on $BridgePort."
}

$healthCheck = @'
import os
from backend.flow_bridge.config import load_config
import httpx

cfg = load_config()
headers = {"Authorization": "Bearer " + cfg["api_key"]}
host = os.getenv("TH_MEDIA_FLOW_BRIDGE_HOST", "127.0.0.1")
port = int(os.getenv("TH_MEDIA_FLOW_BRIDGE_PORT", "8765"))
base = f"http://{host}:{port}"

health = httpx.get(base + "/v1/health", headers=headers, timeout=10)
health.raise_for_status()
session = httpx.get(base + "/v1/session", headers=headers, timeout=30)
session.raise_for_status()

health_data = health.json()
session_data = session.json()
authenticated = bool(session_data.get("authenticated"))

print("FLOW_RUNTIME_HEALTH", health_data)
print("FLOW_SESSION_STATE", session_data.get("state"))
print("FLOW_SESSION_AUTHENTICATED", authenticated)

if not authenticated:
    print("FLOW_REAUTH_REQUIRED", True)
    print("FLOW_LOGIN_WINDOW_OPENED", False)
else:
    print("FLOW_REAUTH_REQUIRED", False)
'@

$healthCheck | python -
