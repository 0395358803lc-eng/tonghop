$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root

$Data = if ($env:TH_MEDIA_DATA_DIR) {
  [Environment]::ExpandEnvironmentVariables($env:TH_MEDIA_DATA_DIR)
} else {
  Join-Path $Root '.data'
}
$Port = if ($env:TH_MEDIA_FLOW_BRIDGE_PORT) { [int]$env:TH_MEDIA_FLOW_BRIDGE_PORT } else { 8765 }
$HostName = if ($env:TH_MEDIA_FLOW_BRIDGE_HOST) { $env:TH_MEDIA_FLOW_BRIDGE_HOST } else { '127.0.0.1' }

New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Rotate-Log([string]$Path) {
  $one = "$Path.1"; $two = "$Path.2"; $three = "$Path.3"
  if (Test-Path $three) { Remove-Item $three -Force }
  if (Test-Path $two) { Move-Item $two $three -Force }
  if (Test-Path $one) { Move-Item $one $two -Force }
  if (Test-Path $Path) { Move-Item $Path $one -Force }
}

$OutLog = Join-Path $Data 'flow_bridge_runtime.out.log'
$ErrLog = Join-Path $Data 'flow_bridge_runtime.err.log'
Rotate-Log $OutLog
Rotate-Log $ErrLog

$proc = Start-Process -FilePath 'python' -ArgumentList @(
  '-m','uvicorn','flow_bridge.app:app','--app-dir','backend','--host',$HostName,'--port',"$Port"
) -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -NoNewWindow -PassThru -Wait
exit $proc.ExitCode
