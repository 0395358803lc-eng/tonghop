$ErrorActionPreference = "Stop"
$Desktop = Resolve-Path (Join-Path $PSScriptRoot "..")
$Key = Join-Path $env:LOCALAPPDATA "TH Media\Signing\updater.key"
$Sample = Join-Path $env:TEMP "th-media-signer-smoke.txt"
Set-Content -LiteralPath $Sample -Value "TH Media updater signer smoke" -Encoding UTF8
Set-Location $Desktop
& npx tauri signer sign -f $Key --app-version 0.1.0 $Sample
if ($LASTEXITCODE -ne 0) { throw "Signer smoke failed." }
if (-not (Test-Path "$Sample.sig")) { throw "Signer smoke signature missing." }
Write-Output "SIGNER_SMOKE=PASS"
Remove-Item $Sample,"$Sample.sig" -Force -ErrorAction SilentlyContinue
