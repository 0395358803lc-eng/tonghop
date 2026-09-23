param(
    [Parameter(Mandatory=$true)][string]$File,
    [string]$Thumbprint = "",
    [string]$TimestampUrl = "",
    [switch]$VerifyOnly
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
    $candidates = foreach ($root in $roots) {
        if (Test-Path $root) {
            Get-ChildItem $root -Recurse -File -Filter signtool.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" -or $_.DirectoryName -match "App Certification Kit$" }
        }
    }
    $picked = $candidates | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $picked) { throw "signtool.exe not found. Install Windows SDK Signing Tools." }
    return $picked.FullName
}

$Target = (Resolve-Path -LiteralPath $File).Path
$SignTool = Find-SignTool

if (-not $VerifyOnly) {
    if (-not $Thumbprint) {
        throw "TH_MEDIA_CODE_SIGN_THUMBPRINT is not configured. A trusted OV/EV code-signing certificate is required."
    }
    $certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Thumbprint -and $_.HasPrivateKey } |
        Select-Object -First 1
    if (-not $certificate) {
        throw "Code-signing certificate $Thumbprint with private key was not found."
    }
    & $SignTool sign /sha1 $Thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d "TH Media" $Target
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed: $Target" }
}

& $SignTool verify /pa /all /v $Target
if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed: $Target" }
Write-Output "SIGNED_AND_VERIFIED=$Target"
