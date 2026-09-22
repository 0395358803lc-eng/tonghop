param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$Entry = Join-Path $ProjectRoot "desktop\flow_bridge_sidecar.py"
$Dist = Join-Path $ProjectRoot "desktop\sidecars"
$Work = Join-Path $ProjectRoot "desktop\.build\pyinstaller-flow"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$Args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", "th-media-flow-bridge",
    "--distpath", $Dist,
    "--workpath", $Work,
    "--specpath", $Work,
    "--paths", (Join-Path $ProjectRoot "backend"),
    "--collect-all", "playwright",
    "--collect-all", "PIL",
    "--collect-submodules", "httpx",
    $Entry
)

& python @Args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller Flow Bridge build failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $Dist "th-media-flow-bridge\th-media-flow-bridge.exe"
if (-not (Test-Path $Exe)) {
    throw "Flow Bridge sidecar executable was not created: $Exe"
}

Write-Output $Exe
