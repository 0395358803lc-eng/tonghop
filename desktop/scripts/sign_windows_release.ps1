param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [ValidateSet("PreBundle","PostBundle","Verify")][string]$Phase = "Verify",
    [string]$CertificateThumbprint = "",
    [string]$TimestampUrl = ""
)

$ErrorActionPreference = "Stop"
if (-not $CertificateThumbprint) { $CertificateThumbprint = $env:TH_MEDIA_CODE_SIGN_THUMBPRINT }
if (-not $CertificateThumbprint) { $CertificateThumbprint = $env:TH_MEDIA_CODE_SIGN_CERT_THUMBPRINT }
if (-not $CertificateThumbprint) { $CertificateThumbprint = $env:TH_MEDIA_CODE_SIGNING_THUMBPRINT }
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_CODE_SIGN_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = "http://timestamp.digicert.com" }

$Preflight = Join-Path $PSScriptRoot "signing_preflight.ps1"
if ($CertificateThumbprint) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Preflight -Thumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Preflight -TimestampUrl $TimestampUrl
}
if ($LASTEXITCODE -ne 0) { throw "Authenticode signing preflight failed." }

$Config = Get-Content (Join-Path $ProjectRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$Version = [string]$Config.version
$Desktop = Join-Path $ProjectRoot "desktop"
$ReleaseRoot = Join-Path $Desktop "src-tauri\target\release"
$BackendExe = Join-Path $Desktop "sidecars\th-media-backend\th-media-backend.exe"
$FlowExe = Join-Path $Desktop "sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"
$MainExe = Join-Path $ReleaseRoot "th-media-desktop.exe"
$Installer = Join-Path $ReleaseRoot ("bundle\nsis\TH Media_" + $Version + "_x64-setup.exe")
$SignOne = Join-Path $PSScriptRoot "sign_windows.ps1"

function Sign-Artifact([string]$Path) {
    if (-not (Test-Path $Path -PathType Leaf)) { throw "Release artifact missing: $Path" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SignOne -File $Path -Thumbprint $CertificateThumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed: $Path" }
}

function Verify-Artifact([string]$Path) {
    if (-not (Test-Path $Path -PathType Leaf)) { throw "Release artifact missing: $Path" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SignOne -File $Path -VerifyOnly
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed: $Path" }
}

switch ($Phase) {
    "PreBundle" {
        Sign-Artifact $BackendExe
        Sign-Artifact $FlowExe
        Sign-Artifact $MainExe
        Write-Output "AUTHENTICODE_PREBUNDLE=PASS"
    }
    "PostBundle" {
        Verify-Artifact $BackendExe
        Verify-Artifact $FlowExe
        Verify-Artifact $MainExe
        Sign-Artifact $Installer
        Write-Output "AUTHENTICODE_POSTBUNDLE=PASS"
    }
    "Verify" {
        foreach ($Artifact in @($BackendExe,$FlowExe,$MainExe,$Installer)) {
            Verify-Artifact $Artifact
        }
        Write-Output "AUTHENTICODE_VERIFY=PASS"
    }
}
