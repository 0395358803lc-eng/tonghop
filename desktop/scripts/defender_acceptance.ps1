param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$status = Get-MpComputerStatus
if (-not $status.AntivirusEnabled -or -not $status.RealTimeProtectionEnabled) {
    throw "Microsoft Defender Antivirus/Real-Time Protection is not enabled."
}

$before = @(Get-MpThreatDetection -ErrorAction SilentlyContinue | ForEach-Object { $_.DetectionID })
$Desktop = Join-Path $ProjectRoot "desktop"
$Config = Get-Content -LiteralPath (Join-Path $Desktop "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$Version = [string]$Config.version
$targets = @(
    (Join-Path $Desktop "src-tauri\target\release\bundle\nsis\TH Media_${Version}_x64-setup.exe"),
    (Join-Path $Desktop "sidecars\th-media-backend"),
    (Join-Path $Desktop "sidecars\th-media-flow-bridge")
)

foreach ($target in $targets) {
    if (-not (Test-Path $target)) { throw "Defender scan target missing: $target" }
    Write-Output "DEFENDER_SCAN=$target"
    Start-MpScan -ScanType CustomScan -ScanPath $target
}

$after = @(Get-MpThreatDetection -ErrorAction SilentlyContinue)
$new = @($after | Where-Object { $_.DetectionID -notin $before })
if ($new.Count -gt 0) {
    $new | Select-Object DetectionID,ThreatID,Resources,InitialDetectionTime | Format-List | Out-String | Write-Output
    throw "Microsoft Defender reported $($new.Count) new detection(s) for TH Media release artifacts."
}

Write-Output "DEFENDER_ACCEPTANCE=PASS"
Write-Output "UPX=DISABLED"
Write-Output "REALTIME_PROTECTION=ENABLED"
