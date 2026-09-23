param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$Version = "",
    [string]$Notes = "TH Media desktop update",
    [string]$Repository = "0395358803lc-eng/tonghop"
)

$ErrorActionPreference = "Stop"
$DesktopRoot = Join-Path $ProjectRoot "desktop"
$ConfigPath = Join-Path $DesktopRoot "src-tauri\tauri.conf.json"
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if (-not $Version) { $Version = [string]$Config.version }
$BundleDir = Join-Path $DesktopRoot "src-tauri\target\release\bundle\nsis"
$Installer = Join-Path $BundleDir "TH Media_${Version}_x64-setup.exe"
$SignaturePath = "$Installer.sig"
if (-not (Test-Path $Installer -PathType Leaf)) {
    throw "Installer not found: $Installer"
}

$KeyPath = Join-Path $env:LOCALAPPDATA "TH Media\Signing\updater.key"
if (-not (Test-Path $KeyPath -PathType Leaf)) {
    throw "Updater private key not found outside the source tree: $KeyPath"
}

Push-Location $DesktopRoot
try {
    & npx tauri signer sign -f $KeyPath --password= --app-version $Version $Installer
    if ($LASTEXITCODE -ne 0) { throw "Updater signing failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
if (-not (Test-Path $SignaturePath -PathType Leaf)) {
    throw "Signature was not created: $SignaturePath"
}

$Signature = (Get-Content -LiteralPath $SignaturePath -Raw).Trim()
$EncodedName = [Uri]::EscapeDataString([IO.Path]::GetFileName($Installer)).Replace("%2F", "/")
$DownloadUrl = "https://github.com/$Repository/releases/download/v$Version/$EncodedName"
$Published = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$Platform = [ordered]@{ url = $DownloadUrl; signature = $Signature }
$Manifest = [ordered]@{
    version = $Version
    notes = $Notes
    pub_date = $Published
    platforms = [ordered]@{
        "windows-x86_64-nsis" = $Platform
        "windows-x86_64" = $Platform
    }
}
$Latest = Join-Path $BundleDir "latest.json"
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Latest -Encoding UTF8

Write-Output "UPDATE_INSTALLER=$Installer"
Write-Output "UPDATE_SIGNATURE=$SignaturePath"
Write-Output "UPDATE_MANIFEST=$Latest"
Write-Output "GITHUB_TAG=v$Version"
