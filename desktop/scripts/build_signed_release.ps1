param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$SigningKeyPath = (Join-Path $env:LOCALAPPDATA "TH Media\Signing\updater.key"),
    [string]$CertificateThumbprint = "",
    [string]$TimestampUrl = "",
    [string]$Notes = "TH Media desktop update",
    [switch]$Fast
)

$ErrorActionPreference = "Stop"
$DesktopRoot = Join-Path $ProjectRoot "desktop"
$Scripts = Join-Path $DesktopRoot "scripts"

if (-not $CertificateThumbprint) { $CertificateThumbprint = $env:TH_MEDIA_CODE_SIGN_THUMBPRINT }
if (-not $CertificateThumbprint) { $CertificateThumbprint = $env:TH_MEDIA_CODE_SIGN_CERT_THUMBPRINT }
if (-not $CertificateThumbprint) { $CertificateThumbprint = $env:TH_MEDIA_CODE_SIGNING_THUMBPRINT }
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_CODE_SIGN_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = "http://timestamp.digicert.com" }

if (-not (Test-Path $SigningKeyPath -PathType Leaf)) {
    throw "Updater signing key not found: $SigningKeyPath"
}
$PrivateKey = Get-Content -Raw -LiteralPath $SigningKeyPath
if (-not $PrivateKey.Trim()) {
    throw "Updater signing key is empty."
}

$Preflight = Join-Path $Scripts "signing_preflight.ps1"
if ($CertificateThumbprint) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Preflight -Thumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Preflight -TimestampUrl $TimestampUrl
}
if ($LASTEXITCODE -ne 0) {
    throw "Production Authenticode preflight failed. Release build was not started."
}

$Previous = @{
    TAURI_SIGNING_PRIVATE_KEY = $env:TAURI_SIGNING_PRIVATE_KEY
    TH_MEDIA_CODE_SIGN_THUMBPRINT = $env:TH_MEDIA_CODE_SIGN_THUMBPRINT
    TH_MEDIA_TIMESTAMP_URL = $env:TH_MEDIA_TIMESTAMP_URL
    TH_MEDIA_REQUIRE_CODE_SIGN = $env:TH_MEDIA_REQUIRE_CODE_SIGN
}

try {
    $env:TAURI_SIGNING_PRIVATE_KEY = $PrivateKey
    $env:TH_MEDIA_CODE_SIGN_THUMBPRINT = $CertificateThumbprint
    $env:TH_MEDIA_TIMESTAMP_URL = $TimestampUrl
    $env:TH_MEDIA_REQUIRE_CODE_SIGN = "1"

    Set-Location $DesktopRoot

    if ($Fast) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Scripts "prepare_release.ps1") -ProjectRoot $ProjectRoot
        if ($LASTEXITCODE -ne 0) { throw "Release preparation failed." }
        & npx tauri build --no-bundle -c '.\tauri.fast-build.conf.json'
    } else {
        & npx tauri build --no-bundle
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri application build failed with exit code $LASTEXITCODE"
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Scripts "sign_windows_release.ps1") -ProjectRoot $ProjectRoot -Phase PreBundle -CertificateThumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Pre-bundle Authenticode signing failed." }

    if ($Fast) {
        & npx tauri bundle -b nsis -c '.\tauri.fast-build.conf.json'
    } else {
        & npx tauri bundle -b nsis
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri NSIS bundle failed with exit code $LASTEXITCODE"
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Scripts "sign_windows_release.ps1") -ProjectRoot $ProjectRoot -Phase PostBundle -CertificateThumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Post-bundle Authenticode signing failed." }

    $Config = Get-Content -LiteralPath (Join-Path $DesktopRoot "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
    $Version = [string]$Config.version
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Scripts "prepare_update_release.ps1") -ProjectRoot $ProjectRoot -Version $Version -Notes $Notes
    if ($LASTEXITCODE -ne 0) { throw "Updater signing/manifest generation failed." }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Scripts "sign_windows_release.ps1") -ProjectRoot $ProjectRoot -Phase Verify -CertificateThumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Final Authenticode verification failed." }

    $Installer = Join-Path $DesktopRoot "src-tauri\target\release\bundle\nsis\TH Media_${Version}_x64-setup.exe"
    Write-Output "SIGNED_RELEASE_READY=$Installer"
    Write-Output "AUTHENTICODE_STATUS=SIGNED_AND_VERIFIED"
    Write-Output "UPDATER_SIGNATURE_STATUS=SIGNED"
} finally {
    foreach ($name in $Previous.Keys) {
        $value = $Previous[$name]
        if ($null -eq $value) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    $PrivateKey = $null
}
