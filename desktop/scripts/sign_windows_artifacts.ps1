param(
    [Parameter(Mandatory=$true)][string[]]$Paths,
    [string]$Thumbprint = "",
    [string]$TimestampUrl = ""
)

$ErrorActionPreference = "Stop"
if (-not $Thumbprint) { $Thumbprint = $env:TH_MEDIA_CODE_SIGN_THUMBPRINT }
if (-not $Thumbprint) { $Thumbprint = $env:TH_MEDIA_CODE_SIGN_CERT_THUMBPRINT }
if (-not $Thumbprint) { $Thumbprint = $env:TH_MEDIA_CODE_SIGNING_THUMBPRINT }
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = $env:TH_MEDIA_CODE_SIGN_TIMESTAMP_URL }
if (-not $TimestampUrl) { $TimestampUrl = "http://timestamp.digicert.com" }
if (-not $Thumbprint) { throw "TH_MEDIA_CODE_SIGN_THUMBPRINT is not configured." }

$SignOne = Join-Path $PSScriptRoot "sign_windows.ps1"
foreach ($Path in $Paths) {
    if (-not (Test-Path $Path -PathType Leaf)) { throw "Artifact not found: $Path" }
    & powershell -NoProfile -ExecutionPolicy Bypass -File $SignOne -File $Path -Thumbprint $Thumbprint -TimestampUrl $TimestampUrl
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing/verification failed: $Path" }
}
