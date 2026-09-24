param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [string]$Root = "",
    [int]$TimeoutSeconds = 60,
    [switch]$SkipUninstall
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "INSTALLER_SMOKE_MISSING: $Installer"
}
$Installer = (Resolve-Path -LiteralPath $Installer).Path

if ([string]::IsNullOrWhiteSpace($Root)) {
    $base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
    $Root = Join-Path $base ("th-media-installed-smoke-" + [Guid]::NewGuid().ToString("N"))
}
$InstallDir = Join-Path $Root "app"
$LocalData = Join-Path $Root "LocalAppData"
New-Item -ItemType Directory -Force -Path $InstallDir,$LocalData | Out-Null

$MainExe = Join-Path $InstallDir "th-media-desktop.exe"
$BackendExe = Join-Path $InstallDir "sidecars\th-media-backend\th-media-backend.exe"
$FlowExe = Join-Path $InstallDir "sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"
$Ffmpeg = Join-Path $InstallDir "runtime\bin\ffmpeg.exe"
$Ffprobe = Join-Path $InstallDir "runtime\bin\ffprobe.exe"
$Speaker = Join-Path $InstallDir "runtime\models\speaker\3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
$WhisperDir = Join-Path $InstallDir "runtime\models\whisper\base"
$BundledBrowser = Join-Path $InstallDir "runtime\browser\chromium\chrome.exe"
$BrowserRoot = Split-Path $BundledBrowser

function Get-OwnProcesses {
    # Only processes that provably belong to this installation: the exe paths are
    # inside the install dir, or a browser launched with this run's isolated
    # profile. A developer's own Chrome or TH Media must not fail the gate.
    @(Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir, [System.StringComparison]::OrdinalIgnoreCase)) -or
        ($_.Name -in @("chrome.exe", "msedge.exe") -and $_.CommandLine -and
            ($_.CommandLine -like "*$LocalData*" -or $_.CommandLine -like "*$BrowserRoot*"))
    })
}

function Assert-NoOrphanProcesses([string]$Phase) {
    $left = @(Get-OwnProcesses)
    if ($left.Count) {
        $detail = ($left | ForEach-Object { "$($_.ProcessId):$($_.Name)" }) -join ", "
        throw "INSTALLER_SMOKE_ORPHAN_PROCESSES after ${Phase} -> $detail"
    }
    Write-Output "INSTALLER_SMOKE_ORPHANS_AFTER_$($Phase.ToUpper().Replace('-','_'))=0"
}

function Get-Descendants([int]$ParentId) {
    $all = @(Get-CimInstance Win32_Process)
    $result = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($ParentId)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        foreach ($child in $all | Where-Object { [int]$_.ParentProcessId -eq $current }) {
            $pidValue = [int]$child.ProcessId
            if (-not $result.Contains($pidValue)) {
                $result.Add($pidValue)
                $queue.Enqueue($pidValue)
            }
        }
    }
    return @($result)
}

function Stop-Tree([int]$ParentId) {
    foreach ($pidValue in @(Get-Descendants $ParentId | Sort-Object -Descending)) {
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $ParentId -Force -ErrorAction SilentlyContinue
}

function Get-DataTreeSignature([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $null }
    $entries = @(Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object FullName |
        ForEach-Object { "{0}|{1}|{2}" -f $_.FullName.Substring($Path.Length), $_.Length, $_.LastWriteTimeUtc.Ticks })
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($entries -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-HttpStatus([string]$Uri) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5 | Out-Null
        return 200
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

$Old = @{
    PATH = $env:PATH
    LOCALAPPDATA = $env:LOCALAPPDATA
    PYTHONHOME = $env:PYTHONHOME
    PYTHONPATH = $env:PYTHONPATH
    NODE_PATH = $env:NODE_PATH
    TH_MEDIA_AUTH_TOKEN = $env:TH_MEDIA_AUTH_TOKEN
    TH_MEDIA_FLOW_BRIDGE_PORT = $env:TH_MEDIA_FLOW_BRIDGE_PORT
    TH_MEDIA_FLOW_BRIDGE_LOG_LEVEL = $env:TH_MEDIA_FLOW_BRIDGE_LOG_LEVEL
}
$Main = $null
$Flow = $null

try {
    # Nothing in this phase may touch the real user profile: the NSIS hooks read
    # and write $LOCALAPPDATA during install and uninstall, and the app creates
    # its database there. PATH is stripped of python/node/npm so the run proves
    # the package is self-contained.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    $env:LOCALAPPDATA = $LocalData
    $env:TH_MEDIA_AUTH_TOKEN = "smoke_" + [Guid]::NewGuid().ToString("N")
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:NODE_PATH -ErrorAction SilentlyContinue

    $ToolVisibility = [ordered]@{
        python = [bool](Get-Command python.exe -ErrorAction SilentlyContinue)
        node = [bool](Get-Command node.exe -ErrorAction SilentlyContinue)
        npm = [bool](Get-Command npm.cmd -ErrorAction SilentlyContinue)
    }
    if ($ToolVisibility.python -or $ToolVisibility.node -or $ToolVisibility.npm) {
        throw "INSTALLER_SMOKE_PATH_NOT_CLEAN: $($ToolVisibility | ConvertTo-Json -Compress)"
    }

    $Install = Start-Process -FilePath $Installer -ArgumentList ("/S /D=" + $InstallDir) -PassThru -Wait
    if ($Install.ExitCode -ne 0) {
        throw "INSTALLER_SMOKE_INSTALL_FAILED: $($Install.ExitCode)"
    }

    foreach ($Required in @($MainExe,$BackendExe,$FlowExe,$Ffmpeg,$Ffprobe,$Speaker,$BundledBrowser)) {
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
            throw "INSTALLER_SMOKE_RESOURCE_MISSING: $Required"
        }
    }
    foreach ($WhisperFile in @("model.bin","config.json","tokenizer.json")) {
        if (-not (Test-Path -LiteralPath (Join-Path $WhisperDir $WhisperFile) -PathType Leaf)) {
            throw "INSTALLER_SMOKE_WHISPER_INCOMPLETE: $(Join-Path $WhisperDir $WhisperFile)"
        }
    }
    if (@(Get-ChildItem -LiteralPath $WhisperDir -File -Filter "vocabulary*").Count -eq 0) {
        throw "INSTALLER_SMOKE_WHISPER_INCOMPLETE: no vocabulary file in $WhisperDir"
    }
    $WhisperModelMb = [math]::Round((Get-Item -LiteralPath (Join-Path $WhisperDir "model.bin")).Length / 1MB, 1)
    if ($WhisperModelMb -lt 50) { throw "INSTALLER_SMOKE_WHISPER_TOO_SMALL: $WhisperModelMb MB" }

    # Existence is not enough for the console tools: they must run from the
    # install tree with no system FFmpeg on PATH. The bundled browser is checked
    # structurally instead, because launching chrome.exe on a machine that
    # already runs Chrome hands the call to that instance and reports
    # "Opening in existing browser session" rather than a version.
    foreach ($Probe in @(
        @{ Name = "FFMPEG"; Exe = $Ffmpeg; Args = @("-version"); Pattern = "ffmpeg" },
        @{ Name = "FFPROBE"; Exe = $Ffprobe; Args = @("-version"); Pattern = "ffprobe" }
    )) {
        # `Select-Object -First 1` would stop the native command mid-stream and
        # leave $LASTEXITCODE at -1, so all output is captured before taking a line.
        $probeRaw = & $Probe.Exe @($Probe.Args) 2>&1
        $probeExit = $LASTEXITCODE
        $probeLines = @($probeRaw)
        $probeOut = if ($probeLines.Count -gt 0) { [string]$probeLines[0] } else { "" }
        if ($probeExit -ne 0 -or $probeOut -notmatch $Probe.Pattern) {
            throw "INSTALLER_SMOKE_BINARY_EXEC_FAILED: $($Probe.Name) exit=$probeExit out=$probeOut"
        }
        Write-Output "INSTALLER_SMOKE_$($Probe.Name)=$($probeOut -replace '\s+', ' ')"
    }
    $BrowserRoot = Split-Path $BundledBrowser
    foreach ($BrowserPart in @("chrome.exe", "chrome.dll")) {
        if (-not (Test-Path -LiteralPath (Join-Path $BrowserRoot $BrowserPart) -PathType Leaf)) {
            throw "INSTALLER_SMOKE_BUNDLED_BROWSER_INCOMPLETE: missing $BrowserPart"
        }
    }
    $BrowserManifest = @(Get-ChildItem -LiteralPath $BrowserRoot -File -Filter "*.manifest" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+(\.\d+){3}\.manifest$' })
    if ($BrowserManifest.Count -eq 0) { throw "INSTALLER_SMOKE_BUNDLED_BROWSER_INCOMPLETE: no versioned manifest" }
    $BrowserBytes = (Get-ChildItem -LiteralPath $BrowserRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
    if ($BrowserBytes -lt 250MB) {
        throw "INSTALLER_SMOKE_BUNDLED_BROWSER_TOO_SMALL: $([math]::Round($BrowserBytes / 1MB, 1)) MB"
    }
    Write-Output "INSTALLER_SMOKE_BUNDLED_CHROME=$($BrowserManifest[0].BaseName) sizeMB=$([math]::Round($BrowserBytes / 1MB, 1))"

    # The packaged sidecars must be able to import the modules the application
    # only reaches at runtime; PyInstaller cannot follow those statically.
    foreach ($Audit in @(@{ Name = "BACKEND"; Exe = $BackendExe }, @{ Name = "FLOW"; Exe = $FlowExe })) {
        $auditRaw = & $Audit.Exe --import-audit 2>&1
        $auditExit = $LASTEXITCODE
        $auditText = ($auditRaw | Out-String)
        if ($auditExit -ne 0) {
            throw "INSTALLER_SMOKE_IMPORT_AUDIT_FAILED $($Audit.Name) exit=$auditExit -> $($auditText -replace '\s+', ' ')"
        }
        $auditReport = $auditText | ConvertFrom-Json
        Write-Output "INSTALLER_SMOKE_IMPORT_AUDIT_$($Audit.Name)=checked $($auditReport.checked), failed=$(@($auditReport.failed).Count)"
    }

    $StartedAt = Get-Date
    $Main = Start-Process -FilePath $MainExe -WorkingDirectory $InstallDir -PassThru
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $Backend = $null
    $Port = 0
    $DbPath = Join-Path $LocalData "TH Media\Desktop\Database\aihub.db"

    while ((Get-Date) -lt $Deadline) {
        $Main.Refresh()
        if ($Main.HasExited) { throw "INSTALLER_SMOKE_MAIN_EXITED: $($Main.ExitCode)" }

        $Desc = @(Get-Descendants $Main.Id)
        $Children = @(Get-CimInstance Win32_Process | Where-Object { $Desc -contains [int]$_.ProcessId })
        $Backend = $Children | Where-Object { $_.Name -eq "th-media-backend.exe" } | Select-Object -First 1
        $EagerFlow = $Children | Where-Object { $_.Name -eq "th-media-flow-bridge.exe" } | Select-Object -First 1

        if ($Backend) {
            $Listener = Get-NetTCPConnection -State Listen -OwningProcess ([int]$Backend.ProcessId) -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($Listener) { $Port = [int]$Listener.LocalPort }
        }
        if ($Backend -and $Port -gt 0 -and (Test-Path -LiteralPath $DbPath -PathType Leaf)) { break }
        Start-Sleep -Milliseconds 300
    }

    if (-not $Backend -or $Port -le 0) { throw "INSTALLER_SMOKE_BACKEND_NOT_READY" }
    if (-not (Test-Path -LiteralPath $DbPath -PathType Leaf)) { throw "INSTALLER_SMOKE_DATABASE_NOT_CREATED" }
    if ($EagerFlow) { throw "INSTALLER_SMOKE_FLOW_STARTED_EAGERLY" }
    $EagerBrowsers = @(Get-CimInstance Win32_Process | Where-Object {
        $Desc -contains [int]$_.ProcessId -and $_.Name -in @("chrome.exe","msedge.exe")
    })
    if ($EagerBrowsers.Count) { throw "INSTALLER_SMOKE_BROWSER_STARTED_EAGERLY: $($EagerBrowsers.Count)" }

    $Listeners = @(Get-NetTCPConnection -State Listen -OwningProcess ([int]$Backend.ProcessId) -ErrorAction SilentlyContinue)
    if (-not $Listeners.Count) { throw "INSTALLER_SMOKE_BACKEND_NO_LOOPBACK_LISTENER" }
    if (@($Listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1","::1") }).Count) {
        throw "INSTALLER_SMOKE_NON_LOOPBACK_LISTENER"
    }

    $BackendStatus = Get-HttpStatus "http://127.0.0.1:$Port/api/health"
    if ($BackendStatus -notin @(200, 401)) { throw "INSTALLER_SMOKE_BACKEND_HTTP_UNEXPECTED: $BackendStatus" }

    Stop-Tree $Main.Id
    $Main = $null
    Start-Sleep -Milliseconds 800
    Assert-NoOrphanProcesses "app-close"

    # The Flow sidecar must also boot from the installed package. Port 0 lets
    # Windows assign it and the probe reads the real listener back.
    $env:TH_MEDIA_FLOW_BRIDGE_PORT = "0"
    $env:TH_MEDIA_FLOW_BRIDGE_LOG_LEVEL = "warning"
    $Flow = Start-Process -FilePath $FlowExe -WorkingDirectory (Split-Path $FlowExe) -PassThru
    $FlowDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $FlowPort = 0
    while ((Get-Date) -lt $FlowDeadline) {
        $Flow.Refresh()
        if ($Flow.HasExited) { throw "INSTALLER_SMOKE_FLOW_EXITED: $($Flow.ExitCode)" }
        # A PyInstaller launcher can hand the socket to a child process, so the
        # listener is looked up across the whole sidecar tree rather than one PID.
        $FlowPids = @([int]$Flow.Id) + @(Get-Descendants ([int]$Flow.Id))
        $FlowListener = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $FlowPids -contains [int]$_.OwningProcess }) | Select-Object -First 1
        if ($FlowListener) { $FlowPort = [int]$FlowListener.LocalPort; break }
        Start-Sleep -Milliseconds 300
    }
    if ($FlowPort -le 0) { throw "INSTALLER_SMOKE_FLOW_NOT_READY" }
    $FlowPids = @([int]$Flow.Id) + @(Get-Descendants ([int]$Flow.Id))
    $FlowListeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $FlowPids -contains [int]$_.OwningProcess })
    if (@($FlowListeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1","::1") }).Count) {
        throw "INSTALLER_SMOKE_FLOW_NON_LOOPBACK_LISTENER"
    }

    # A fresh install has no stored Flow API key, so 401 is the expected answer
    # and still proves uvicorn + the FastAPI app booted with auth enforced.
    $FlowStatus = Get-HttpStatus "http://127.0.0.1:$FlowPort/v1/health"
    if ($FlowStatus -notin @(200, 401)) { throw "INSTALLER_SMOKE_FLOW_HTTP_UNEXPECTED: $FlowStatus" }
    if (@(Get-CimInstance Win32_Process | Where-Object { (Get-Descendants ([int]$Flow.Id)) -contains [int]$_.ProcessId -and $_.Name -in @("chrome.exe","msedge.exe") }).Count) {
        throw "INSTALLER_SMOKE_FLOW_BROWSER_STARTED_WITHOUT_LOGIN"
    }
    Stop-Tree ([int]$Flow.Id)
    $Flow = $null
    Start-Sleep -Milliseconds 800
    Assert-NoOrphanProcesses "flow-stop"

    $Elapsed = [math]::Round(((Get-Date) - $StartedAt).TotalMilliseconds)
    Write-Output "INSTALLER_SMOKE_STARTUP_MS=$Elapsed"
    Write-Output "INSTALLER_SMOKE_BACKEND_PORT=$Port"
    Write-Output "INSTALLER_SMOKE_BACKEND_HTTP=$BackendStatus"
    Write-Output "INSTALLER_SMOKE_FLOW_PORT=$FlowPort"
    Write-Output "INSTALLER_SMOKE_FLOW_HTTP=$FlowStatus"
    Write-Output "INSTALLER_SMOKE_WHISPER_MB=$WhisperModelMb"
    Write-Output "INSTALLER_SMOKE_TOOLS_VISIBLE=$($ToolVisibility | ConvertTo-Json -Compress)"
    Write-Output "INSTALLER_SMOKE_INSTALLED=PASS"

    if ($SkipUninstall) {
        Write-Output "INSTALLER_SMOKE_ROOT=$Root"
        Write-Output "INSTALLER_SMOKE=PASS"
        exit 0
    }

    $Uninstaller = Join-Path $InstallDir "uninstall.exe"
    if (-not (Test-Path -LiteralPath $Uninstaller -PathType Leaf)) {
        throw "INSTALLER_SMOKE_UNINSTALLER_MISSING: $Uninstaller"
    }
    $DataBefore = Get-DataTreeSignature $LocalData
    if (-not $DataBefore) { throw "INSTALLER_SMOKE_DATA_ROOT_MISSING_BEFORE_UNINSTALL" }
    $DbHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $DbPath).Hash
    $FileCountBefore = @(Get-ChildItem -LiteralPath $LocalData -Recurse -File -ErrorAction SilentlyContinue).Count

    # NSIS defaults the first prompt to "Keep Data" (/SD IDYES), so a silent
    # uninstall must remove the program files and preserve the data root.
    $Uninstall = Start-Process -FilePath $Uninstaller -ArgumentList "/S" -PassThru -Wait
    if ($Uninstall.ExitCode -ne 0) {
        throw "INSTALLER_SMOKE_UNINSTALL_FAILED: $($Uninstall.ExitCode)"
    }
    Start-Sleep -Milliseconds 800

    if (Test-Path -LiteralPath $MainExe -PathType Leaf) { throw "INSTALLER_SMOKE_EXECUTABLE_NOT_REMOVED" }
    if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) { throw "INSTALLER_SMOKE_UNINSTALLER_NOT_REMOVED" }
    if (-not (Test-Path -LiteralPath $DbPath -PathType Leaf)) { throw "INSTALLER_SMOKE_KEEP_DATA_LOST_DATABASE" }

    $DbHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $DbPath).Hash
    $DataAfter = Get-DataTreeSignature $LocalData
    $FileCountAfter = @(Get-ChildItem -LiteralPath $LocalData -Recurse -File -ErrorAction SilentlyContinue).Count

    Write-Output "INSTALLER_SMOKE_UNINSTALL=PASS"
    Write-Output "INSTALLER_SMOKE_KEEP_DATA_TREE_STABLE=$($DataBefore -eq $DataAfter)"
    Write-Output "INSTALLER_SMOKE_KEEP_DATA_DB_STABLE=$($DbHashBefore -eq $DbHashAfter)"
    Write-Output "INSTALLER_SMOKE_KEEP_DATA_FILES=$FileCountBefore->$FileCountAfter"
    if ($DataBefore -ne $DataAfter) { throw "INSTALLER_SMOKE_KEEP_DATA_TREE_CHANGED: $DataBefore -> $DataAfter" }
    if ($DbHashBefore -ne $DbHashAfter) { throw "INSTALLER_SMOKE_KEEP_DATA_DB_CHANGED" }
    Assert-NoOrphanProcesses "uninstall"
    Write-Output "INSTALLER_SMOKE=PASS"
}
finally {
    if ($Flow) { Stop-Tree ([int]$Flow.Id) }
    if ($Main) { Stop-Tree $Main.Id }
    foreach ($name in $Old.Keys) {
        if ($null -eq $Old[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $Old[$name] }
    }
    Write-Output "INSTALLER_SMOKE_ROOT=$Root"
}
