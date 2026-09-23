param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$Version = "0.1.0",
    [string]$SigningKeyPath = (Join-Path $env:LOCALAPPDATA "TH Media\Signing\updater.key")
)

$ErrorActionPreference = "Stop"
$DesktopRoot = Join-Path $ProjectRoot "desktop"
$Artifact = Join-Path $DesktopRoot ("src-tauri\target\release\bundle\nsis\TH Media_" + $Version + "_x64-setup.exe")
if (-not (Test-Path $Artifact -PathType Leaf)) { throw "Updater artifact not found." }
if (-not (Test-Path $SigningKeyPath -PathType Leaf)) { throw "Updater key not found." }

$PreviousPassword = [Environment]::GetEnvironmentVariable("TAURI_SIGNING_PRIVATE_KEY_PASSWORD")

try {
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ""
    Set-Location $DesktopRoot
    & npx tauri signer sign -f $SigningKeyPath --app-version $Version $Artifact
    if ($LASTEXITCODE -ne 0) { throw "Updater artifact signing failed." }
} finally {
    if ($null -eq $PreviousPassword) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
    } else {
        $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $PreviousPassword
    }
}

$SignaturePath = "$Artifact.sig"
if (-not (Test-Path $SignaturePath -PathType Leaf)) { throw "Signature file was not created." }
Write-Output "SIGNED_UPDATER=$Artifact"
Write-Output "SIGNATURE_FILE=$SignaturePath"
