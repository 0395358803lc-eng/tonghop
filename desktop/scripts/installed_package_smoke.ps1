param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "INSTALLER_SMOKE_MISSING: $Installer"
}

$Root = Join-Path $env:RUNNER_TEMP ("th-media-installed-smoke-" + [Guid]::NewGuid().ToString("N"))
$InstallDir = Join-Path $Root "app"
$LocalData = Join-Path $Root "LocalAppData"
New-Item -ItemType Directory -Force -Path $InstallDir,$LocalData | Out-Null

$Install = Start-Process -FilePath $Installer -ArgumentList ("/S /D=" + $InstallDir) -PassThru -Wait
if ($Install.ExitCode -ne 0) {
    throw "INSTALLER_SMOKE_INSTALL_FAILED: $($Install.ExitCode)"
}

$MainExe = Join-Path $InstallDir "th-media-desktop.exe"
$BackendExe = Join-Path $InstallDir "sidecars\th-media-backend\th-media-backend.exe"
$FlowExe = Join-Path $InstallDir "sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"
$Ffmpeg = Join-Path $InstallDir "runtime\bin\ffmpeg.exe"
$Ffprobe = Join-Path $InstallDir "runtime\bin\ffprobe.exe"
$Speaker = Join-Path $InstallDir "runtime\models\speaker\3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
$Whisper = Join-Path $InstallDir "runtime\models\whisper\base\model.bin"
$WhisperConfig = Join-Path $InstallDir "runtime\models\whisper\base\config.json"

foreach ($Required in @($MainExe,$BackendExe,$FlowExe,$Ffmpeg,$Ffprobe,$Speaker,$Whisper,$WhisperConfig)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "INSTALLER_SMOKE_RESOURCE_MISSING: $Required"
    }
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

$OldLocal = $env:LOCALAPPDATA
$OldToken = $env:TH_MEDIA_AUTH_TOKEN
$env:LOCALAPPDATA = $LocalData
$env:TH_MEDIA_AUTH_TOKEN = "smoke_" + [Guid]::NewGuid().ToString("N")
$Main = $null

try {
    $StartedAt = Get-Date
    $Main = Start-Process -FilePath $MainExe -WorkingDirectory $InstallDir -PassThru
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $Backend = $null
    $Port = 0
    $Flow = $null

    while ((Get-Date) -lt $Deadline) {
        $Main.Refresh()
        if ($Main.HasExited) { throw "INSTALLER_SMOKE_MAIN_EXITED: $($Main.ExitCode)" }

        $Desc = @(Get-Descendants $Main.Id)
        $Children = @(Get-CimInstance Win32_Process | Where-Object { $Desc -contains [int]$_.ProcessId })
        $Backend = $Children | Where-Object { $_.Name -eq "th-media-backend.exe" } | Select-Object -First 1
        $Flow = $Children | Where-Object { $_.Name -eq "th-media-flow-bridge.exe" } | Select-Object -First 1

        if ($Backend) {
            $Listener = Get-NetTCPConnection -State Listen -OwningProcess ([int]$Backend.ProcessId) -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($Listener) { $Port = [int]$Listener.LocalPort }
        }
        $DbPath = Join-Path $LocalData "TH Media\Desktop\Database\aihub.db"
        if ($Backend -and $Port -gt 0 -and (Test-Path -LiteralPath $DbPath -PathType Leaf)) { break }
        Start-Sleep -Milliseconds 300
    }

    if (-not $Backend -or $Port -le 0) { throw "INSTALLER_SMOKE_BACKEND_NOT_READY" }
    if ($Flow) { throw "INSTALLER_SMOKE_FLOW_STARTED_EAGERLY" }

    $Listeners = @(Get-NetTCPConnection -State Listen -OwningProcess ([int]$Backend.ProcessId) -ErrorAction SilentlyContinue)
    $NonLoopback = @($Listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1","::1") })
    if ($NonLoopback.Count) { throw "INSTALLER_SMOKE_NON_LOOPBACK_LISTENER" }

    $Elapsed = [math]::Round(((Get-Date) - $StartedAt).TotalMilliseconds)
    Write-Output "INSTALLER_SMOKE_STARTUP_MS=$Elapsed"
    Write-Output "INSTALLER_SMOKE_BACKEND_PORT=$Port"
    Write-Output "INSTALLER_SMOKE_WHISPER_MB=$([math]::Round((Get-Item $Whisper).Length / 1MB, 1))"
    Write-Output "INSTALLER_SMOKE=PASS"
}
finally {
    if ($Main) {
        try {
            $Main.Refresh()
            if (-not $Main.HasExited) {
                $Desc = @(Get-Descendants $Main.Id)
                foreach ($pidValue in ($Desc | Sort-Object -Descending)) {
                    Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
                }
                Stop-Process -Id $Main.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }
    if ($null -eq $OldLocal) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue } else { $env:LOCALAPPDATA = $OldLocal }
    if ($null -eq $OldToken) { Remove-Item Env:TH_MEDIA_AUTH_TOKEN -ErrorAction SilentlyContinue } else { $env:TH_MEDIA_AUTH_TOKEN = $OldToken }
}

$Uninstaller = Join-Path $InstallDir "uninstall.exe"
if (Test-Path -LiteralPath $Uninstaller) {
    $Uninstall = Start-Process -FilePath $Uninstaller -ArgumentList "/S" -PassThru -Wait
    if ($Uninstall.ExitCode -ne 0) {
        throw "INSTALLER_SMOKE_UNINSTALL_FAILED: $($Uninstall.ExitCode)"
    }
}
