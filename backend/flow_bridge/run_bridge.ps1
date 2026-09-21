$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root
$Data = Join-Path $Root '.data'
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
  '-m','uvicorn','flow_bridge.app:app','--app-dir','backend','--host','127.0.0.1','--port','8765'
) -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -NoNewWindow -PassThru -Wait
exit $proc.ExitCode
