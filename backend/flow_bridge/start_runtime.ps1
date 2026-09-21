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

python backend\flow_bridge\bootstrap.py | Out-Host

$cdpListener = Get-NetTCPConnection -State Listen -LocalPort 9223 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($cdpListener) {
  $cdpProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($cdpListener.OwningProcess)" -ErrorAction SilentlyContinue
  if (-not $cdpProcess -or $cdpProcess.CommandLine -notlike '*flow_chrome_profile*') {
    throw "Port 9223 is occupied by another process. Runtime will not stop or replace it."
  }
}

$bridgeListener = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bridgeListener) {
  $bridgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($bridgeListener.OwningProcess)" -ErrorAction SilentlyContinue
  if (-not $bridgeProcess -or $bridgeProcess.CommandLine -notlike '*uvicorn*flow_bridge.app:app*') {
    throw "Port 8765 is occupied by another process. Runtime will not stop or replace it."
  }
}

if (-not $cdpListener) {
  & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'launch_chrome.ps1') | Out-Host
  if (-not (Wait-Port 9223 20)) { throw 'Flow Chrome CDP failed to start on 127.0.0.1:9223.' }
} else {
  Write-Output 'Flow Chrome CDP already listening on 9223.'
}

if (-not (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue)) {
  $bridgeScript = Join-Path $PSScriptRoot 'run_bridge.ps1'
  Start-Process powershell -WindowStyle Hidden -ArgumentList @('-ExecutionPolicy','Bypass','-File',('"' + $bridgeScript + '"'))
  if (-not (Wait-Port 8765 20)) { throw 'Flow Bridge failed to start on 127.0.0.1:8765.' }
} else {
  Write-Output 'Flow Bridge already listening on 8765.'
}

$healthCheck = @'
from backend.flow_bridge.config import load_config
import httpx

cfg = load_config()
headers = {"Authorization": "Bearer " + cfg["api_key"]}
base = "http://127.0.0.1:8765"

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
