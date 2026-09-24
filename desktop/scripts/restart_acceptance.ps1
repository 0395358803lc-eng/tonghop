param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [int]$StartupTimeoutSeconds = 45,
    [int]$ShutdownTimeoutSeconds = 15
)

$ErrorActionPreference = "Stop"
$ReleaseRoot = Join-Path $ProjectRoot "desktop\src-tauri\target\release"
$MainExe = Join-Path $ReleaseRoot "th-media-desktop.exe"
$FingerprintScript = Join-Path $ProjectRoot "desktop\scripts\acceptance\restart_fingerprint.py"

if (-not (Test-Path $MainExe -PathType Leaf)) { throw "RESTART_ACCEPTANCE_MAIN_EXE_MISSING" }
if (-not (Test-Path $FingerprintScript -PathType Leaf)) { throw "RESTART_ACCEPTANCE_FINGERPRINT_SCRIPT_MISSING" }

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

function Get-Fingerprint {
    $raw = & python $FingerprintScript
    if ($LASTEXITCODE -ne 0) { throw "RESTART_FINGERPRINT_FAILED" }
    return ($raw | ConvertFrom-Json)
}

function Start-And-Snapshot([string]$Label) {
    $Token = "restart_$([Guid]::NewGuid().ToString('N'))"
    $PreviousToken = $env:TH_MEDIA_AUTH_TOKEN
    $env:TH_MEDIA_AUTH_TOKEN = $Token
    $Main = $null
    try {
        $StartedAt = Get-Date
        $Main = Start-Process -FilePath $MainExe -WorkingDirectory $ReleaseRoot -PassThru
        $Deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        $Backend = $null
        $Flow = $null
        $BackendPort = 0
        $FlowPort = 0
        while ((Get-Date) -lt $Deadline) {
            $Main.Refresh()
            if ($Main.HasExited) { throw "$Label MAIN_EXITED_EARLY exit=$($Main.ExitCode)" }
            $Desc = @(Get-DescendantProcessIds $Main.Id)
            $Children = @(Get-CimInstance Win32_Process | Where-Object { $Desc -contains [int]$_.ProcessId })
            $Backend = $Children | Where-Object { $_.Name -eq "th-media-backend.exe" } | Select-Object -First 1
            $Flow = $Children | Where-Object { $_.Name -eq "th-media-flow-bridge.exe" } | Select-Object -First 1
            if ($Backend) {
                $BackendPort = [int]((Get-NetTCPConnection -State Listen -OwningProcess ([int]$Backend.ProcessId) -ErrorAction SilentlyContinue | Select-Object -First 1).LocalPort)
            }
            if ($Flow) {
                $FlowPort = [int]((Get-NetTCPConnection -State Listen -OwningProcess ([int]$Flow.ProcessId) -ErrorAction SilentlyContinue | Select-Object -First 1).LocalPort)
            }
            if ($BackendPort -gt 0 -and $FlowPort -gt 0) { break }
            Start-Sleep -Milliseconds 400
        }
        if ($BackendPort -le 0) { throw "$Label BACKEND_NOT_READY" }
        if ($FlowPort -le 0) { throw "$Label FLOW_NOT_READY" }

        $Headers = @{ "X-TH-Media-Token" = $Token }
        $Ready = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/ready" -Headers $Headers -TimeoutSec 12
        $FlowStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/flow" -Headers $Headers -TimeoutSec 12
        $FlowSessions = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/flow/sessions" -Headers $Headers -TimeoutSec 12
        $Fingerprint = Get-Fingerprint

        $Snapshot = [ordered]@{
            label = $Label
            startup_ms = [math]::Round(((Get-Date) - $StartedAt).TotalMilliseconds)
            main_pid = $Main.Id
            backend_pid = [int]$Backend.ProcessId
            flow_pid = [int]$Flow.ProcessId
            backend_port = $BackendPort
            flow_port = $FlowPort
            ready = $Ready
            flow = $FlowStatus
            flow_sessions = $FlowSessions
            fingerprint = $Fingerprint
        }

        $Process = Get-Process -Id $Main.Id -ErrorAction Stop
        if (-not $Process.CloseMainWindow()) { throw "$Label CLOSE_MAIN_WINDOW_FAILED" }
        $StopDeadline = (Get-Date).AddSeconds($ShutdownTimeoutSeconds)
        while ((Get-Date) -lt $StopDeadline) {
            Start-Sleep -Milliseconds 300
            $Process.Refresh()
            if ($Process.HasExited) { break }
        }
        if (-not $Process.HasExited) {
            throw "$Label GRACEFUL_SHUTDOWN_TIMEOUT"
        }
        $Snapshot["graceful_shutdown"] = $true
        $Snapshot["exit_code"] = $Process.ExitCode
        return [pscustomobject]$Snapshot
    }
    finally {
        if ($Main) {
            try {
                $Main.Refresh()
                if (-not $Main.HasExited) {
                    $Desc = @(Get-DescendantProcessIds $Main.Id)
                    foreach ($pidValue in ($Desc | Sort-Object -Descending)) {
                        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
                    }
                    Stop-Process -Id $Main.Id -Force -ErrorAction SilentlyContinue
                }
            } catch {}
        }
        if ($null -eq $PreviousToken) { Remove-Item Env:TH_MEDIA_AUTH_TOKEN -ErrorAction SilentlyContinue }
        else { $env:TH_MEDIA_AUTH_TOKEN = $PreviousToken }
    }
}

$Before = Get-Fingerprint
$Before | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $ProjectRoot 'desktop\.build\restart_before.json') -Encoding UTF8
$First = Start-And-Snapshot "restart-1"
$Between = Get-Fingerprint
$Between | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $ProjectRoot 'desktop\.build\restart_between.json') -Encoding UTF8
$Second = Start-And-Snapshot "restart-2"
$After = Get-Fingerprint
$After | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $ProjectRoot 'desktop\.build\restart_after.json') -Encoding UTF8

$StableFirst = $Before.fingerprint -eq $Between.fingerprint
$StableSecond = $Between.fingerprint -eq $After.fingerprint
$ProjectsSame = (@($First.fingerprint.projects | ForEach-Object id) -join ",") -eq (@($Second.fingerprint.projects | ForEach-Object id) -join ",")
$SelectedSame = (@($First.fingerprint.selected_media | ForEach-Object id) -join ",") -eq (@($Second.fingerprint.selected_media | ForEach-Object id) -join ",")
$QueuesSame = (ConvertTo-Json $First.fingerprint.queues -Compress -Depth 8) -eq (ConvertTo-Json $Second.fingerprint.queues -Compress -Depth 8)
$FinalsSame = (@($First.fingerprint.finals | ForEach-Object id) -join ",") -eq (@($Second.fingerprint.finals | ForEach-Object id) -join ",")
$FlowSessionsSame = (ConvertTo-Json $First.fingerprint.flow_sessions -Compress -Depth 8) -eq (ConvertTo-Json $Second.fingerprint.flow_sessions -Compress -Depth 8)

$Result = [ordered]@{
    ok = ($StableFirst -and $StableSecond -and $ProjectsSame -and $SelectedSame -and $QueuesSame -and $FinalsSame -and $FlowSessionsSame -and $First.graceful_shutdown -and $Second.graceful_shutdown)
    before_fingerprint = $Before.fingerprint
    after_restart_1_fingerprint = $Between.fingerprint
    after_restart_2_fingerprint = $After.fingerprint
    stable_restart_1 = $StableFirst
    stable_restart_2 = $StableSecond
    project_ids_preserved = $ProjectsSame
    selected_media_preserved = $SelectedSame
    queues_preserved = $QueuesSame
    final_renders_preserved = $FinalsSame
    flow_sessions_preserved = $FlowSessionsSame
    project_count = @($After.projects).Count
    selected_media_count = @($After.selected_media).Count
    render_job_count = @($After.render_jobs).Count
    pipeline_run_count = @($After.runs).Count
    final_render_count = @($After.finals).Count
    provider_count = @($After.providers).Count
    flow_profile_nonempty = [bool]$After.flow_profile_nonempty
    restart_1_startup_ms = $First.startup_ms
    restart_2_startup_ms = $Second.startup_ms
    restart_1_ready = [bool]$First.ready.ready
    restart_2_ready = [bool]$Second.ready.ready
    restart_1_flow_authenticated = [bool]$First.ready.checks.flow_authenticated
    restart_2_flow_authenticated = [bool]$Second.ready.checks.flow_authenticated
    restart_1_graceful_shutdown = [bool]$First.graceful_shutdown
    restart_2_graceful_shutdown = [bool]$Second.graceful_shutdown
}
$Result | ConvertTo-Json -Depth 6
if (-not $Result.ok) { exit 1 }
