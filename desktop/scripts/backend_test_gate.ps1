param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string[]]$Modules = @(),
    [switch]$Quiet
)

# Runs the backend/flow test suite with the DB isolation gate on, then proves no real store moved.
# A green suite that rewrote the acceptance database is not green, so this exits non-zero on either.

$ErrorActionPreference = "Stop"
if (Test-Path "Variable:PSNativeCommandUseErrorActionPreference") { $PSNativeCommandUseErrorActionPreference = $false }

function Get-StoreState([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ Path = $Path; Present = $false; Hash = "absent"; Bytes = 0 }
    }
    $item = Get-Item -LiteralPath $Path
    [pscustomobject]@{
        Path    = $Path
        Present = $true
        Hash    = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        Bytes   = $item.Length
    }
}

function Format-StoreState($State) {
    if (-not $State.Present) { return "absent" }
    "$($State.Hash.Substring(0,16))/$($State.Bytes)B"
}

$Backend = Join-Path $ProjectRoot "backend"
if (-not (Test-Path -LiteralPath $Backend -PathType Container)) { throw "Backend directory missing: $Backend" }

if (-not $Modules -or $Modules.Count -eq 0) {
    Push-Location $Backend
    try {
        $Modules = @(
            (Get-ChildItem "app\test_*.py" | ForEach-Object { "app." + $_.BaseName }) +
            (Get-ChildItem "flow_bridge\test_*.py" | ForEach-Object { "flow_bridge." + $_.BaseName })
        )
    }
    finally { Pop-Location }
}
else {
    # -File binds "a,b,c" as a single element, so split whatever arrived into real module names.
    $Modules = @($Modules | ForEach-Object { $_ -split "[,\s]+" } | Where-Object { $_ })
}
if ($Modules.Count -eq 0) { throw "No test modules discovered under $Backend" }

# The installed store belongs to a running desktop app; if one is live its hash changes for
# reasons unrelated to the test suite, so enforcing equality there would be a false alarm.
$AppIsRunning = [bool](Get-Process -Name "th-media-backend" -ErrorAction SilentlyContinue)
$Watched = New-Object System.Collections.Generic.List[object]
$Watched.Add([pscustomobject]@{ Name = "dev-scratch"; Enforce = $true; Files = @(
    (Join-Path $ProjectRoot ".data\aihub.db"), (Join-Path $ProjectRoot ".data\aihub.db-wal")
) })
if ($env:LOCALAPPDATA) {
    $InstalledDir = Join-Path $env:LOCALAPPDATA "TH Media\Desktop\Database"
    $Watched.Add([pscustomobject]@{
        Name    = "installed-product"
        Enforce = (-not $AppIsRunning)
        Files   = @((Join-Path $InstalledDir "aihub.db"), (Join-Path $InstalledDir "aihub.db-wal"))
    })
}

$Before = [ordered]@{}
foreach ($Store in $Watched) {
    foreach ($File in $Store.Files) { $Before[$File] = Get-StoreState $File }
}

$env:TH_MEDIA_REQUIRE_DB_ISOLATION = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Output "[TH Media] Backend test gate: $($Modules.Count) modules, isolation gate ON"
$ExitCode = 0
Push-Location $Backend
try {
    python -m unittest @Modules
    $ExitCode = $LASTEXITCODE
}
finally { Pop-Location }

$Leaked = New-Object System.Collections.Generic.List[string]
$Skipped = New-Object System.Collections.Generic.List[string]
foreach ($Store in $Watched) {
    foreach ($File in $Store.Files) {
        $After = Get-StoreState $File
        $Was = $Before[$File]
        $changed = ($Was.Hash -ne $After.Hash) -or ($Was.Bytes -ne $After.Bytes)
        if ($changed -and -not $Store.Enforce) {
            Write-Output "PROD_DB_UNTOUCHED=SKIPPED store=$($Store.Name) path=$File reason=desktop-app-running"
            $Skipped.Add("$($Store.Name) $File")
            continue
        }
        if ($changed) {
            Write-Output "PROD_DB_UNTOUCHED=FAIL store=$($Store.Name) path=$File before=$(Format-StoreState $Was) after=$(Format-StoreState $After)"
            $Leaked.Add("$($Store.Name) $File")
        }
        else {
            Write-Output "PROD_DB_UNTOUCHED=PASS store=$($Store.Name) path=$File $(Format-StoreState $After)"
        }
    }
}

if ($ExitCode -ne 0) { Write-Output "BACKEND_TEST_GATE=FAIL unit-test-exit=$ExitCode"; exit 1 }
if ($Leaked.Count -gt 0) {
    Write-Output "BACKEND_TEST_GATE=FAIL unisolated-writes=$($Leaked.Count)"
    foreach ($Entry in $Leaked) { Write-Output "  $Entry" }
    exit 1
}
Write-Output "BACKEND_TEST_GATE=PASS modules=$($Modules.Count) skipped=$($Skipped.Count)"
exit 0
