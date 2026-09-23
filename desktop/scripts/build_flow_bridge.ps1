param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$PrepareRuntime = Join-Path $ProjectRoot "desktop\scripts\prepare_runtime_dependencies.ps1"
& $PrepareRuntime -ProjectRoot $ProjectRoot

$Entry = Join-Path $ProjectRoot "desktop\flow_bridge_sidecar.py"
$Dist = Join-Path $ProjectRoot "desktop\sidecars"
$Work = Join-Path $ProjectRoot "desktop\.build\pyinstaller-flow"

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$TargetDir = Join-Path $Dist "th-media-flow-bridge"
$Running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($TargetDir, [System.StringComparison]::OrdinalIgnoreCase)
})
if ($Running.Count) {
    $Details = ($Running | ForEach-Object { "PID=$($_.ProcessId) $($_.ExecutablePath)" }) -join "; "
    throw "Flow Bridge sidecar đang chạy và khóa release directory. Hãy đóng TH Media trước khi build. $Details"
}

$Args = @(
    "--noconfirm",
    "--clean",
    "--noupx",
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

$Runner = Join-Path $ProjectRoot "desktop\scripts\pyinstaller_isolated.py"
& python -S -P $Runner @Args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller Flow Bridge build failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $Dist "th-media-flow-bridge\th-media-flow-bridge.exe"
if (-not (Test-Path $Exe)) {
    throw "Flow Bridge sidecar executable was not created: $Exe"
}

$Internal = Join-Path $Dist "th-media-flow-bridge\_internal"
foreach ($Required in @("python3.dll", "vcruntime140.dll")) {
    if (-not (Test-Path (Join-Path $Internal $Required))) {
        throw "Flow Bridge sidecar missing required runtime: $Required"
    }
}

Write-Output $Exe
