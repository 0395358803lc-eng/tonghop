param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$Repository = "0395358803lc-eng/tonghop",
    [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
$ConfigPath = Join-Path $ProjectRoot "desktop\src-tauri\tauri.conf.json"
$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$Version = [string]$Config.version
if (-not $Version) { throw "Missing version in tauri.conf.json" }

$BundleDir = Join-Path $ProjectRoot "desktop\src-tauri\target\release\bundle\nsis"
$Installer = Get-ChildItem $BundleDir -File -Filter "*$Version*x64-setup.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Installer) { throw "NSIS updater artifact not found for version $Version" }

$SignaturePath = "$($Installer.FullName).sig"
if (-not (Test-Path $SignaturePath -PathType Leaf)) {
    throw "Updater signature not found: $SignaturePath"
}
$SignatureInfo = Get-Item -LiteralPath $SignaturePath
if ($SignatureInfo.LastWriteTimeUtc -lt $Installer.LastWriteTimeUtc) {
    throw "Updater signature is stale: signature timestamp is older than installer. Re-sign the current installer."
}
$Signature = (Get-Content -Raw -LiteralPath $SignaturePath).Trim()
if (-not $Signature) { throw "Updater signature is empty." }

$EncodedName = [Uri]::EscapeDataString($Installer.Name)
$DownloadUrl = "https://github.com/$Repository/releases/download/v$Version/$EncodedName"
$Manifest = [ordered]@{
    version = $Version
    notes = $Notes
    pub_date = [DateTimeOffset]::UtcNow.ToString("o")
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = $Signature
            url = $DownloadUrl
        }
    }
}

$ReleaseDir = Join-Path $ProjectRoot "desktop\release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$LatestJson = Join-Path $ReleaseDir "latest.json"
$Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $LatestJson -Encoding UTF8

Copy-Item $Installer.FullName (Join-Path $ReleaseDir $Installer.Name) -Force
Copy-Item $SignaturePath (Join-Path $ReleaseDir ([IO.Path]::GetFileName($SignaturePath))) -Force

Write-Output "UPDATE_VERSION=$Version"
Write-Output "UPDATE_ARTIFACT=$($Installer.Name)"
Write-Output "UPDATE_MANIFEST=$LatestJson"
