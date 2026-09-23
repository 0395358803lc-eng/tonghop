$ErrorActionPreference = "Stop"
$SigningDir = Join-Path $env:LOCALAPPDATA "TH Media\Signing"
$KeyPath = Join-Path $SigningDir "updater.key"
$PasswordPath = Join-Path $SigningDir "updater.password.dpapi"
New-Item -ItemType Directory -Force -Path $SigningDir | Out-Null

$Bytes = New-Object byte[] 32
$Rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
try { $Rng.GetBytes($Bytes) } finally { $Rng.Dispose() }
$Password = [Convert]::ToBase64String($Bytes)
$Secure = ConvertTo-SecureString $Password -AsPlainText -Force
$ProtectedPassword = $Secure | ConvertFrom-SecureString
Set-Content -LiteralPath $PasswordPath -Value $ProtectedPassword -Encoding UTF8

$PreviousPassword = [Environment]::GetEnvironmentVariable("TAURI_SIGNING_PRIVATE_KEY_PASSWORD")

try {
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $Password
    Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))
    & npx tauri signer generate -p $Password -w $KeyPath -f --ci
    if ($LASTEXITCODE -ne 0) { throw "Updater key generation failed." }
} finally {
    if ($null -eq $PreviousPassword) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
    } else {
        $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $PreviousPassword
    }
    $Password = $null
    $Secure = $null
}

if (-not (Test-Path "$KeyPath.pub" -PathType Leaf)) { throw "Updater public key missing." }
Write-Output "UPDATER_KEY_ROTATED=PASS"
Write-Output "PUBLIC_KEY_FILE=$KeyPath.pub"
