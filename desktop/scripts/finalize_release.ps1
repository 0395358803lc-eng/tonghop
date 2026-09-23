param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$Notes = "TH Media desktop update"
)

$ErrorActionPreference = "Stop"
$Desktop = Join-Path $ProjectRoot "desktop"
$Config = Get-Content -LiteralPath (Join-Path $Desktop "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$Version = [string]$Config.version
$Thumbprint = $env:TH_MEDIA_CODE_SIGN_THUMBPRINT
if (-not $Thumbprint) { $Thumbprint = $env:TH_MEDIA_CODE_SIGN_CERT_THUMBPRINT }
if (-not $Thumbprint) { $Thumbprint = $env:TH_MEDIA_CODE_SIGNING_THUMBPRINT }
$TimestampUrl = $env:TH_MEDIA_TIMESTAMP_URL
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_CODE_SIGN_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = "http://timestamp.digicert.com" }
$RequireSigning = [Environment]::GetEnvironmentVariable("TH_MEDIA_REQUIRE_CODE_SIGN") -eq "1"

$MainExe = Join-Path $Desktop "src-tauri\target\release\th-media-desktop.exe"
$Installer = Join-Path $Desktop "src-tauri\target\release\bundle\nsis\TH Media_${Version}_x64-setup.exe"
$BackendExe = Join-Path $Desktop "sidecars\th-media-backend\th-media-backend.exe"
$FlowExe = Join-Path $Desktop "sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"

foreach ($Required in @($MainExe, $Installer, $BackendExe, $FlowExe)) {
    if (-not (Test-Path $Required -PathType Leaf)) { throw "Release artifact missing: $Required" }
}

if ($Thumbprint) {
    $SignRelease = Join-Path $Desktop "scripts\sign_windows_release.ps1"

    foreach ($Target in @($BackendExe,$FlowExe,$MainExe)) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Desktop "scripts\sign_windows.ps1") -File $Target -VerifyOnly
        if ($LASTEXITCODE -ne 0) {
            throw "Pre-bundle artifact is not already Authenticode-signed: $Target. Rebuild with build_signed_release.ps1."
        }
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File $SignRelease -ProjectRoot $ProjectRoot -Phase PostBundle -CertificateThumbprint $Thumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Installer Authenticode signing failed." }

    & powershell -NoProfile -ExecutionPolicy Bypass -File $SignRelease -ProjectRoot $ProjectRoot -Phase Verify -CertificateThumbprint $Thumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Final Authenticode verification failed." }
} elseif ($RequireSigning) {
    throw "Production release is blocked: TH_MEDIA_CODE_SIGN_THUMBPRINT is missing."
} else {
    Write-Warning "Authenticode certificate is not configured. This local release remains unsigned."
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Desktop "scripts\prepare_update_release.ps1") -ProjectRoot $ProjectRoot -Version $Version -Notes $Notes
if ($LASTEXITCODE -ne 0) { throw "Updater release preparation failed." }

Write-Output "RELEASE_READY=$Installer"
Write-Output ("AUTHENTICODE_STATUS=" + $(if ($Thumbprint) { "SIGNED_AND_VERIFIED" } else { "BLOCKED_BY_CERT" }))
