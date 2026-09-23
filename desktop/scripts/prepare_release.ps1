param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$Frontend = Join-Path $ProjectRoot "frontend"
$BuildBackend = Join-Path $ProjectRoot "desktop\scripts\build_backend.ps1"
$BuildFlow = Join-Path $ProjectRoot "desktop\scripts\build_flow_bridge.ps1"
$Config = Get-Content -LiteralPath (Join-Path $ProjectRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$Version = [string]$Config.version
$BundleDir = Join-Path $ProjectRoot "desktop\src-tauri\target\release\bundle\nsis"
$Installer = Join-Path $BundleDir "TH Media_${Version}_x64-setup.exe"
foreach ($Stale in @((Join-Path $BundleDir "latest.json"), "$Installer.sig", $Installer)) {
    if (Test-Path -LiteralPath $Stale -PathType Leaf) {
        Remove-Item -LiteralPath $Stale -Force
    }
}

Write-Output "[TH Media] Cleared stale installer/update metadata for version $Version."
Write-Output "[TH Media] Building frontend..."
& npm --prefix $Frontend run build
if ($LASTEXITCODE -ne 0) {
    throw "Frontend build failed with exit code $LASTEXITCODE"
}
Write-Output "[TH Media] Building backend sidecar..."
& powershell -NoProfile -ExecutionPolicy Bypass -File $BuildBackend -ProjectRoot $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Backend sidecar build failed with exit code $LASTEXITCODE"
}

Write-Output "[TH Media] Building Flow Bridge sidecar..."
& powershell -NoProfile -ExecutionPolicy Bypass -File $BuildFlow -ProjectRoot $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Flow Bridge sidecar build failed with exit code $LASTEXITCODE"
}
$Runtime = Join-Path $ProjectRoot "desktop\runtime"
$Sidecars = Join-Path $ProjectRoot "desktop\sidecars"
if (-not (Test-Path (Join-Path $Runtime "bin\ffmpeg.exe"))) {
    throw "Release runtime is missing ffmpeg.exe"
}
if (-not (Test-Path (Join-Path $Runtime "bin\ffprobe.exe"))) {
    throw "Release runtime is missing ffprobe.exe"
}
if (-not (Test-Path (Join-Path $Runtime "models\whisper\base\model.bin"))) {
    throw "Release runtime is missing bundled faster-whisper base model"
}
if (-not (Test-Path (Join-Path $Runtime "models\whisper\base\config.json"))) {
    throw "Release runtime is missing bundled faster-whisper base config"
}
if (-not (Test-Path (Join-Path $Sidecars "th-media-backend\th-media-backend.exe"))) {
    throw "Release backend sidecar is missing"
}
if (-not (Test-Path (Join-Path $Sidecars "th-media-flow-bridge\th-media-flow-bridge.exe"))) {
    throw "Release Flow Bridge sidecar is missing"
}

$SignScript = Join-Path $ProjectRoot "desktop\scripts\sign_windows.ps1"
$Thumbprint = [Environment]::GetEnvironmentVariable("TH_MEDIA_CODE_SIGN_THUMBPRINT")
$RequireSigning = [Environment]::GetEnvironmentVariable("TH_MEDIA_REQUIRE_CODE_SIGN") -eq "1"
if ($Thumbprint) {
    foreach ($Target in @(
        (Join-Path $Sidecars "th-media-backend\th-media-backend.exe"),
        (Join-Path $Sidecars "th-media-flow-bridge\th-media-flow-bridge.exe")
    )) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $SignScript -File $Target -Thumbprint $Thumbprint
        if ($LASTEXITCODE -ne 0) { throw "Sidecar signing failed: $Target" }
    }
} elseif ($RequireSigning) {
    throw "Production release requires TH_MEDIA_CODE_SIGN_THUMBPRINT."
} else {
    Write-Warning "Authenticode certificate is not configured. Sidecars remain unsigned for this local build."
}

Write-Output "[TH Media] Release prerequisites are ready."
