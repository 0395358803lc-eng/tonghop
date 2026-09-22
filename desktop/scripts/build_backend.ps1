param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$Entry = Join-Path $ProjectRoot "desktop\backend_sidecar.py"
$Dist = Join-Path $ProjectRoot "desktop\sidecars"
$Work = Join-Path $ProjectRoot "desktop\.build\pyinstaller"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$Args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", "th-media-backend",
    "--distpath", $Dist,
    "--workpath", $Work,
    "--specpath", $Work,
    "--paths", (Join-Path $ProjectRoot "backend"),
    "--collect-all", "sherpa_onnx",
    "--collect-all", "faster_whisper",
    "--collect-all", "av",
    "--collect-submodules", "bgutil_ytdlp_pot_provider",
    $Entry
)

& python @Args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller backend build failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $Dist "th-media-backend\th-media-backend.exe"
if (-not (Test-Path $Exe)) {
    throw "Backend sidecar executable was not created: $Exe"
}

Write-Output $Exe
