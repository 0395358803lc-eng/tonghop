param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$PrepareRuntime = Join-Path $ProjectRoot "desktop\scripts\prepare_runtime_dependencies.ps1"
& $PrepareRuntime -ProjectRoot $ProjectRoot

$Entry = Join-Path $ProjectRoot "desktop\backend_sidecar.py"
$Dist = Join-Path $ProjectRoot "desktop\sidecars"
$Work = Join-Path $ProjectRoot "desktop\.build\pyinstaller"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$TargetDir = Join-Path $Dist "th-media-backend"
$Running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($TargetDir, [System.StringComparison]::OrdinalIgnoreCase)
})
if ($Running.Count) {
    $Details = ($Running | ForEach-Object { "PID=$($_.ProcessId) $($_.ExecutablePath)" }) -join "; "
    throw "Backend sidecar đang chạy và khóa release directory. Hãy đóng TH Media trước khi build. $Details"
}

$Args = @(
    "--noconfirm",
    "--clean",
    "--noupx",
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

$Runner = Join-Path $ProjectRoot "desktop\scripts\pyinstaller_isolated.py"
& python -S -P $Runner @Args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller backend build failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $Dist "th-media-backend\th-media-backend.exe"
if (-not (Test-Path $Exe)) {
    throw "Backend sidecar executable was not created: $Exe"
}

$Internal = Join-Path $Dist "th-media-backend\_internal"
foreach ($Required in @("python3.dll", "vcruntime140.dll")) {
    if (-not (Test-Path (Join-Path $Internal $Required))) {
        throw "Backend sidecar missing required runtime: $Required"
    }
}

Write-Output $Exe
