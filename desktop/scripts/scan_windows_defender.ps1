param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$Config = Get-Content (Join-Path $ProjectRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$Version = [string]$Config.version

function Resolve-MpCmdRun {
    $commonData = [Environment]::GetFolderPath('CommonApplicationData')
    $platformRoot = Join-Path $commonData "Microsoft\Windows Defender\Platform"
    if (Test-Path $platformRoot) {
        $candidate = Get-ChildItem $platformRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "MpCmdRun.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($candidate) { return $candidate }
    }
    $programFiles = [Environment]::GetFolderPath('ProgramFiles')
    $fallback = Join-Path $programFiles "Windows Defender\MpCmdRun.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "Microsoft Defender command-line scanner was not found."
}
$Scanner = Resolve-MpCmdRun
$ReleaseRoot = Join-Path $ProjectRoot "desktop\src-tauri\target\release"
$Artifacts = @(
    (Join-Path $ProjectRoot "desktop\sidecars\th-media-backend\th-media-backend.exe"),
    (Join-Path $ProjectRoot "desktop\sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"),
    (Join-Path $ReleaseRoot "th-media-desktop.exe"),
    (Join-Path $ReleaseRoot ("bundle\nsis\TH Media_" + $Version + "_x64-setup.exe"))
)

foreach ($Artifact in $Artifacts) {
    if (-not (Test-Path $Artifact -PathType Leaf)) {
        throw "Defender scan target missing: $Artifact"
    }
    Write-Output "SCANNING=$Artifact"
    & $Scanner -Scan -ScanType 3 -File $Artifact -DisableRemediation
    if ($LASTEXITCODE -ne 0) {
        throw "Microsoft Defender scan failed or detected a threat for: $Artifact (exit $LASTEXITCODE)"
    }
    Write-Output "DEFENDER_PASS=$Artifact"
}
