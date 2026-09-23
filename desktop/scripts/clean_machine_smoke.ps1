param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$ReleaseRoot = "",
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
if (-not $ReleaseRoot) {
    $ReleaseRoot = Join-Path $ProjectRoot "desktop\src-tauri\target\release"
}
$ReleaseRoot = (Resolve-Path -LiteralPath $ReleaseRoot).Path
$MainExe = Join-Path $ReleaseRoot "th-media-desktop.exe"
$BackendExe = Join-Path $ReleaseRoot "sidecars\th-media-backend\th-media-backend.exe"
$FlowExe = Join-Path $ReleaseRoot "sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"
$FfmpegExe = Join-Path $ReleaseRoot "runtime\bin\ffmpeg.exe"
$FfprobeExe = Join-Path $ReleaseRoot "runtime\bin\ffprobe.exe"

foreach ($required in @($MainExe, $BackendExe, $FlowExe, $FfmpegExe, $FfprobeExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "CLEANROOM_ARTIFACT_MISSING: $required"
    }
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$CleanRoot = Join-Path $ProjectRoot "desktop\.cleanroom\$Stamp"
$LocalAppData = Join-Path $CleanRoot "LocalAppData"
New-Item -ItemType Directory -Force -Path $LocalAppData | Out-Null

$Original = @{
    PATH = $env:PATH
    LOCALAPPDATA = $env:LOCALAPPDATA
    PYTHONHOME = $env:PYTHONHOME
    PYTHONPATH = $env:PYTHONPATH
    NODE_PATH = $env:NODE_PATH
}
$Main = $null

function Get-DescendantProcessIds([int]$ParentId) {
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

try {
    $env:LOCALAPPDATA = $LocalAppData
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:NODE_PATH -ErrorAction SilentlyContinue

    $ToolVisibility = [ordered]@{
        python = [bool](Get-Command python.exe -ErrorAction SilentlyContinue)
        node = [bool](Get-Command node.exe -ErrorAction SilentlyContinue)
        npm = [bool](Get-Command npm.cmd -ErrorAction SilentlyContinue)
    }
    if ($ToolVisibility.python -or $ToolVisibility.node -or $ToolVisibility.npm) {
        throw "CLEANROOM_PATH_NOT_CLEAN: $($ToolVisibility | ConvertTo-Json -Compress)"
    }

    $StartedAt = Get-Date
    $Main = Start-Process -FilePath $MainExe -WorkingDirectory $ReleaseRoot -PassThru
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $Backend = $null
    $Flow = $null
    $AppRoot = Join-Path $LocalAppData "TH Media\Desktop"
    $DbPath = Join-Path $AppRoot "Database\aihub.db"

    while ((Get-Date) -lt $Deadline) {
        if ($Main.HasExited) {
            throw "CLEANROOM_MAIN_EXITED: exit=$($Main.ExitCode)"
        }
        $Descendants = @(Get-DescendantProcessIds -ParentId $Main.Id)
        $Processes = @(Get-CimInstance Win32_Process | Where-Object { $Descendants -contains [int]$_.ProcessId })
        $Backend = $Processes | Where-Object { $_.Name -eq "th-media-backend.exe" } | Select-Object -First 1
        $Flow = $Processes | Where-Object { $_.Name -eq "th-media-flow-bridge.exe" } | Select-Object -First 1
        if ($Backend -and (Test-Path -LiteralPath $DbPath -PathType Leaf)) {
            break
        }
        Start-Sleep -Milliseconds 500
        $Main.Refresh()
    }

    if (-not $Backend) { throw "CLEANROOM_BACKEND_NOT_STARTED" }
    if (-not (Test-Path -LiteralPath $DbPath -PathType Leaf)) { throw "CLEANROOM_DATABASE_NOT_CREATED" }
    if ($Flow) { throw "CLEANROOM_FLOW_STARTED_EAGERLY" }

    $BackendListeners = @(Get-NetTCPConnection -State Listen -OwningProcess ([int]$Backend.ProcessId) -ErrorAction SilentlyContinue)
    if (-not $BackendListeners.Count) { throw "CLEANROOM_BACKEND_NO_LOOPBACK_LISTENER" }

    foreach ($listener in @($BackendListeners)) {
        if ($listener.LocalAddress -notin @("127.0.0.1", "::1")) {
            throw "CLEANROOM_NON_LOOPBACK_LISTENER: $($listener.LocalAddress):$($listener.LocalPort)"
        }
    }

    $BackendPort = [int]($BackendListeners | Select-Object -First 1).LocalPort
    $BackendHttpStatus = 0
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$BackendPort/api/health" -TimeoutSec 4 | Out-Null
        $BackendHttpStatus = 200
    } catch {
        if ($_.Exception.Response) { $BackendHttpStatus = [int]$_.Exception.Response.StatusCode }
    }
    if ($BackendHttpStatus -notin @(200, 401)) { throw "CLEANROOM_BACKEND_HTTP_UNEXPECTED: $BackendHttpStatus" }

    $RuntimeReadyMs = [math]::Round(((Get-Date) - $StartedAt).TotalMilliseconds)
    $MainPs = Get-Process -Id $Main.Id -ErrorAction Stop
    $BackendPs = Get-Process -Id ([int]$Backend.ProcessId) -ErrorAction Stop
    $Descendants = @(Get-DescendantProcessIds -ParentId $Main.Id)
    $ChromeDescendants = @(Get-CimInstance Win32_Process | Where-Object { $Descendants -contains [int]$_.ProcessId -and $_.Name -eq 'chrome.exe' })
    $IdleWorkingSetMb = [math]::Round(($MainPs.WorkingSet64 + $BackendPs.WorkingSet64) / 1MB, 1)

    $Result = [ordered]@{
        ok = $true
        mode = "isolated-localappdata-sanitized-path"
        clean_root = $CleanRoot
        tools_visible = $ToolVisibility
        main_pid = $Main.Id
        backend_pid = [int]$Backend.ProcessId
        flow_pid = $null
        database_created = (Test-Path -LiteralPath $DbPath)
        backend_port = $BackendPort
        flow_port = $null
        backend_http_status = $BackendHttpStatus
        flow_http_status = $null
        runtime_ready_ms = $RuntimeReadyMs
        idle_working_set_mb = $IdleWorkingSetMb
        main_working_set_mb = [math]::Round($MainPs.WorkingSet64 / 1MB, 1)
        backend_working_set_mb = [math]::Round($BackendPs.WorkingSet64 / 1MB, 1)
        flow_working_set_mb = 0
        flow_lazy_pass = (-not $Flow)
        chrome_descendant_count = $ChromeDescendants.Count
        packaged_backend = $Backend.ExecutablePath
        packaged_flow = $FlowExe
        ffmpeg_bundled = (Test-Path -LiteralPath $FfmpegExe)
        ffprobe_bundled = (Test-Path -LiteralPath $FfprobeExe)
    }
    $Result | ConvertTo-Json -Depth 5
}
finally {
    if ($Main) {
        try {
            $Descendants = @(Get-DescendantProcessIds -ParentId $Main.Id)
            foreach ($pidValue in ($Descendants | Sort-Object -Descending)) {
                Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
            }
            Stop-Process -Id $Main.Id -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    $env:PATH = $Original.PATH
    $env:LOCALAPPDATA = $Original.LOCALAPPDATA
    if ($null -ne $Original.PYTHONHOME) { $env:PYTHONHOME = $Original.PYTHONHOME }
    if ($null -ne $Original.PYTHONPATH) { $env:PYTHONPATH = $Original.PYTHONPATH }
    if ($null -ne $Original.NODE_PATH) { $env:NODE_PATH = $Original.NODE_PATH }
}
