param(
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

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) { return $command.Source }

    $roots = @(
        "C:\Program Files (x86)\Windows Kits\10\bin",
        "C:\Program Files (x86)\Windows Kits\10\App Certification Kit",
        "C:\Program Files\Windows Kits\10\bin"
    )
    $candidates = @()
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $candidates += Get-ChildItem $root -Recurse -File -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" -or $_.DirectoryName -match "App Certification Kit$" }
        }
    }
    $picked = $candidates | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $picked) { throw "signtool.exe not found. Install Windows SDK Signing Tools." }
    return $picked.FullName
}

$SignTool = Find-SignTool
if (-not $Thumbprint) {
    throw "TH_MEDIA_CODE_SIGN_THUMBPRINT is not configured. A trusted OV/EV code-signing certificate with private key is required."
}

$Thumbprint = ($Thumbprint -replace "\s", "").ToUpperInvariant()
$Certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Thumbprint -eq $Thumbprint -and
        $_.HasPrivateKey -and
        $_.NotBefore -le (Get-Date) -and
        $_.NotAfter -gt (Get-Date)
    } |
    Select-Object -First 1

if (-not $Certificate) {
    throw "Trusted code-signing certificate $Thumbprint with private key was not found or is not currently valid."
}

Write-Output "SIGNTOOL=$SignTool"
Write-Output "THUMBPRINT=$Thumbprint"
Write-Output "TIMESTAMP_URL=$TimestampUrl"
Write-Output "CERT_SUBJECT=$($Certificate.Subject)"
Write-Output "CERT_NOT_AFTER=$($Certificate.NotAfter.ToString('o'))"
