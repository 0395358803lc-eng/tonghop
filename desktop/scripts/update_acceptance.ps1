param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$InstallerA = "",
    [string]$InstallerB = "",
    [int]$StartupTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$Desktop = Join-Path $ProjectRoot "desktop"
$TestBase = Join-Path $Desktop ".update-test"
$Version = [string]((Get-Content -LiteralPath (Join-Path $Desktop "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version)
if (-not $InstallerA) { $InstallerA = Join-Path $TestBase "TH Media_A_0.1.0_x64-setup.exe" }
if (-not $InstallerB) { $InstallerB = Join-Path $TestBase "TH Media_B_${Version}_x64-setup.exe" }
$FingerprintScript = Join-Path $Desktop "scripts\acceptance\restart_fingerprint.py"
$AssetScript = Join-Path $Desktop "scripts\acceptance\update_asset_fingerprint.py"
$RewriteScript = Join-Path $Desktop "scripts\acceptance\rewrite_cloned_paths.py"
$SourceLocalAppData = $env:LOCALAPPDATA
$SourceDataRoot = Join-Path $SourceLocalAppData "TH Media\Desktop"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunRoot = Join-Path $TestBase "run-$Stamp"
$TestLocalAppData = Join-Path $RunRoot "LocalAppData"
$TestDataRoot = Join-Path $TestLocalAppData "TH Media\Desktop"
$InstallDir = Join-Path $RunRoot "install"
$MetadataBackup = Join-Path $RunRoot "real-installer-metadata"
$ResultPath = Join-Path $TestBase "last-update-result.json"

foreach ($required in @($InstallerA,$InstallerB,$FingerprintScript,$AssetScript,$RewriteScript)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw @"
UPDATE_ACCEPTANCE_MISSING: $required
Place the two real installer artifacts under $TestBase, or pass -InstallerA/-InstallerB:
  A = TH Media_0.1.0_x64-setup.exe  (previous production release)
  B = TH Media_${Version}_x64-setup.exe  (release being accepted, from the GitHub Release page)
The gate compares user state across the upgrade, so neither file may be a source-tree build.
"@
    }
}
if (-not (Test-Path -LiteralPath $SourceDataRoot -PathType Container)) { throw "UPDATE_ACCEPTANCE_SOURCE_DATA_MISSING" }

New-Item -ItemType Directory -Force -Path $RunRoot,$TestLocalAppData,$InstallDir,$MetadataBackup | Out-Null

$RealUpdates = Join-Path $SourceLocalAppData "TH Media\Updates"
$RealInstallerBackups = Join-Path $SourceDataRoot "Backups\Installers"
$HadRealUpdates = Test-Path $RealUpdates
$HadRealInstallerBackups = Test-Path $RealInstallerBackups

function Copy-Tree([string]$Source,[string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { return }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $code = $LASTEXITCODE
    if ($code -gt 7) { throw "ROBOCOPY_FAILED code=$code source=$Source" }
}

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

function Get-MainExe([string]$Root) {
    $candidate = Join-Path $Root "TH Media.exe"
    if (Test-Path $candidate -PathType Leaf) { return $candidate }
    $candidate = Join-Path $Root "th-media-desktop.exe"
    if (Test-Path $candidate -PathType Leaf) { return $candidate }
    $found = Get-ChildItem $Root -File -Filter "*.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch "uninstall|setup|backend|flow" } |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    throw "UPDATE_ACCEPTANCE_MAIN_EXE_NOT_FOUND: $Root"
}

function Get-Fingerprint {
    $raw = & python $FingerprintScript
    if ($LASTEXITCODE -ne 0) { throw "UPDATE_FINGERPRINT_FAILED" }
    return ($raw | ConvertFrom-Json)
}

function Get-AssetFingerprint {
    $raw = & python $AssetScript
    if ($LASTEXITCODE -ne 0) { throw "UPDATE_ASSET_FINGERPRINT_FAILED" }
    return ($raw | ConvertFrom-Json)
}

function Assert-NoSourceMediaLeak($Assets) {
    $source = [IO.Path]::GetFullPath($SourceDataRoot)
    foreach ($item in @($Assets.selected_media_files)) {
        foreach ($field in @($item.file,$item.thumbnail)) {
            if ($field -and $field.path -and [IO.Path]::IsPathRooted([string]$field.path)) {
                $full = [IO.Path]::GetFullPath([string]$field.path)
                if ($full.StartsWith($source,[StringComparison]::OrdinalIgnoreCase)) {
                    throw "UPDATE_ACCEPTANCE_SOURCE_MEDIA_LEAK: $full"
                }
            }
        }
    }
    foreach ($item in @($Assets.final_files)) {
        $field = $item.file
        if ($field -and $field.path -and [IO.Path]::IsPathRooted([string]$field.path)) {
            $full = [IO.Path]::GetFullPath([string]$field.path)
            if ($full.StartsWith($source,[StringComparison]::OrdinalIgnoreCase)) {
                throw "UPDATE_ACCEPTANCE_SOURCE_FINAL_LEAK: $full"
            }
        }
    }
}

function Start-AppSnapshot([string]$MainExe,[string]$Label) {
    $Token = "update_$([Guid]::NewGuid().ToString('N'))"
    $env:TH_MEDIA_AUTH_TOKEN = $Token
    $Main = $null
    try {
        $started = Get-Date
        $Main = Start-Process -FilePath $MainExe -WorkingDirectory (Split-Path $MainExe -Parent) -PassThru
        $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
        $backend = $null
        $backendPort = 0
        while ((Get-Date) -lt $deadline) {
            $Main.Refresh()
            if ($Main.HasExited) { throw "$Label MAIN_EXITED_EARLY exit=$($Main.ExitCode)" }
            $desc = @(Get-DescendantProcessIds $Main.Id)
            $children = @(Get-CimInstance Win32_Process | Where-Object { $desc -contains [int]$_.ProcessId })
            $backend = $children | Where-Object { $_.Name -eq "th-media-backend.exe" } | Select-Object -First 1
            if ($backend) {
                $backendPort = [int]((Get-NetTCPConnection -State Listen -OwningProcess ([int]$backend.ProcessId) -ErrorAction SilentlyContinue | Select-Object -First 1).LocalPort)
            }
            if ($backendPort -gt 0) { break }
            Start-Sleep -Milliseconds 500
        }
        if ($backendPort -le 0) { throw "$Label BACKEND_NOT_READY" }

        $headers = @{ "X-TH-Media-Token" = $Token }
        $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/api/ready" -Headers $headers -TimeoutSec 15
        $fingerprint = Get-Fingerprint
        $assets = Get-AssetFingerprint
        Assert-NoSourceMediaLeak $assets

        $proc = Get-Process -Id $Main.Id -ErrorAction Stop
        if (-not $proc.CloseMainWindow()) { throw "$Label CLOSE_FAILED" }
        $stopDeadline=(Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $stopDeadline) {
            Start-Sleep -Milliseconds 300
            $proc.Refresh()
            if ($proc.HasExited) { break }
        }
        if (-not $proc.HasExited) { throw "$Label GRACEFUL_SHUTDOWN_TIMEOUT" }

        return [pscustomobject]@{
            label=$Label
            startup_ms=[math]::Round(((Get-Date)-$started).TotalMilliseconds)
            ready=$ready
            fingerprint=$fingerprint
            assets=$assets
            graceful_shutdown=$true
            product_version=(Get-Item $MainExe).VersionInfo.ProductVersion
            file_version=(Get-Item $MainExe).VersionInfo.FileVersion
        }
    }
    finally {
        if ($Main) {
            try {
                $Main.Refresh()
                if (-not $Main.HasExited) {
                    $desc=@(Get-DescendantProcessIds $Main.Id)
                    foreach($pidValue in ($desc | Sort-Object -Descending)){Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue}
                    Stop-Process -Id $Main.Id -Force -ErrorAction SilentlyContinue
                }
            } catch {}
        }
    }
}

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Start-FlowProbe([string]$InstalledRoot,[string]$Label) {
    $FlowExe = Join-Path $InstalledRoot "sidecars\th-media-flow-bridge\th-media-flow-bridge.exe"
    if (-not (Test-Path -LiteralPath $FlowExe -PathType Leaf)) { throw "$Label FLOW_EXE_MISSING" }
    $FlowPort = Get-FreePort
    $CdpPort = Get-FreePort
    while ($CdpPort -eq $FlowPort) { $CdpPort = Get-FreePort }
    $Key = "flowprobe_$([Guid]::NewGuid().ToString('N'))"
    $RuntimeBin = Join-Path $InstalledRoot "runtime\bin"
    $SettingsPath = Join-Path $TestDataRoot "desktop-settings.json"
    $ChromePath = ""
    if (Test-Path $SettingsPath -PathType Leaf) {
        try { $ChromePath = [string]((Get-Content $SettingsPath -Raw | ConvertFrom-Json).chrome_path) } catch {}
    }

    $Names = @(
        "TH_MEDIA_DATA_DIR","TH_MEDIA_MEDIA_DIR","TH_MEDIA_FLOW_PROFILE_DIR","TH_MEDIA_FLOW_SESSIONS_DIR",
        "TH_MEDIA_FLOW_REGISTRY_PATH","TH_MEDIA_FLOW_BRIDGE_HOST","TH_MEDIA_FLOW_BRIDGE_PORT","FLOW_BRIDGE_API_KEY",
        "FLOW_CDP_URL","TH_MEDIA_RUNTIME_BIN_DIR","TH_MEDIA_FFMPEG_PATH","TH_MEDIA_FFPROBE_PATH","TH_MEDIA_CHROME_PATH"
    )
    $Saved = @{}
    foreach ($Name in $Names) { $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process") }
    $Flow = $null
    try {
        $env:TH_MEDIA_DATA_DIR = $TestDataRoot
        $env:TH_MEDIA_MEDIA_DIR = Join-Path $TestDataRoot "Media"
        $env:TH_MEDIA_FLOW_PROFILE_DIR = Join-Path $TestDataRoot "FlowProfile"
        $env:TH_MEDIA_FLOW_SESSIONS_DIR = Join-Path $TestDataRoot "FlowSessions"
        $env:TH_MEDIA_FLOW_REGISTRY_PATH = Join-Path $TestDataRoot "Database\flow_sessions.json"
        $env:TH_MEDIA_FLOW_BRIDGE_HOST = "127.0.0.1"
        $env:TH_MEDIA_FLOW_BRIDGE_PORT = [string]$FlowPort
        $env:FLOW_BRIDGE_API_KEY = $Key
        $env:FLOW_CDP_URL = "http://127.0.0.1:$CdpPort"
        $env:TH_MEDIA_RUNTIME_BIN_DIR = $RuntimeBin
        $env:TH_MEDIA_FFMPEG_PATH = Join-Path $RuntimeBin "ffmpeg.exe"
        $env:TH_MEDIA_FFPROBE_PATH = Join-Path $RuntimeBin "ffprobe.exe"
        if ($ChromePath) { $env:TH_MEDIA_CHROME_PATH = $ChromePath } else { Remove-Item Env:TH_MEDIA_CHROME_PATH -ErrorAction SilentlyContinue }

        $Flow = Start-Process -FilePath $FlowExe -WorkingDirectory (Split-Path $FlowExe -Parent) -WindowStyle Hidden -PassThru
        $Deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $Deadline) {
            $Flow.Refresh()
            if ($Flow.HasExited) { throw "$Label FLOW_EXITED_EARLY exit=$($Flow.ExitCode)" }
            if (Get-NetTCPConnection -State Listen -OwningProcess $Flow.Id -ErrorAction SilentlyContinue) { break }
            Start-Sleep -Milliseconds 300
        }
        if (-not (Get-NetTCPConnection -State Listen -OwningProcess $Flow.Id -ErrorAction SilentlyContinue)) { throw "$Label FLOW_PORT_NOT_READY" }

        $Headers = @{ Authorization = "Bearer $Key" }
        $Sessions = Invoke-RestMethod -Uri "http://127.0.0.1:$FlowPort/v1/sessions" -Headers $Headers -TimeoutSec 45
        $Projects = $null
        if ($Sessions.authenticated) {
            $Projects = Invoke-RestMethod -Uri "http://127.0.0.1:$FlowPort/v1/projects" -Headers $Headers -TimeoutSec 45
        }
        return [pscustomobject]@{
            label = $Label
            authenticated = [bool]$Sessions.authenticated
            session_state = [string]$Sessions.session_state
            active_id = [string]$Sessions.active_id
            active_account = [string]$Sessions.active_account
            sessions = @($Sessions.sessions)
            project_count = if ($Projects) { [int]$Projects.count } else { 0 }
            flow_port = $FlowPort
            cdp_port = $CdpPort
        }
    }
    finally {
        try {
            $Headers = @{ Authorization = "Bearer $Key" }
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$FlowPort/v1/runtime/stop-chrome" -Headers $Headers -TimeoutSec 12 | Out-Null
        } catch {}
        if ($Flow) {
            try { $Flow.Refresh(); if (-not $Flow.HasExited) { Stop-Process -Id $Flow.Id -Force -ErrorAction SilentlyContinue } } catch {}
        }
        foreach ($Name in $Names) {
            if ($null -eq $Saved[$Name]) { [Environment]::SetEnvironmentVariable($Name, $null, "Process") }
            else { [Environment]::SetEnvironmentVariable($Name, [string]$Saved[$Name], "Process") }
        }
    }
}

$PreviousLocalAppData = $env:LOCALAPPDATA
$PreviousToken = $env:TH_MEDIA_AUTH_TOKEN
$Uninstaller = $null
$Result = $null

try {
    if ($HadRealUpdates) { Copy-Tree $RealUpdates (Join-Path $MetadataBackup "Updates") }
    if ($HadRealInstallerBackups) { Copy-Tree $RealInstallerBackups (Join-Path $MetadataBackup "InstallerBackups") }

    $a = Start-Process -FilePath $InstallerA -ArgumentList @("/S",("/D="+$InstallDir)) -Wait -PassThru
    if ($a.ExitCode -ne 0) { throw "INSTALL_A_FAILED exit=$($a.ExitCode)" }
    $MainA = Get-MainExe $InstallDir
    $Uninstaller = Get-ChildItem $InstallDir -File -Filter "*uninstall*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

    $dirs = @(
        "Database","FlowProfile","FlowSessions","final_films","flow_downloads","flow_image_downloads",
        "film_assets","generated_media","narrator_tts","speaker_calibration","speaker_embeddings","Media"
    )
    foreach($name in $dirs){
        Copy-Tree (Join-Path $SourceDataRoot $name) (Join-Path $TestDataRoot $name)
    }
    New-Item -ItemType Directory -Force -Path $TestDataRoot | Out-Null
    foreach($name in @("desktop-settings.json","flow_bridge_config.json")){
        $src=Join-Path $SourceDataRoot $name
        if(Test-Path $src -PathType Leaf){Copy-Item -LiteralPath $src -Destination (Join-Path $TestDataRoot $name) -Force}
    }

    & python $RewriteScript $SourceDataRoot $TestDataRoot | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "CLONED_PATH_REWRITE_FAILED" }

    $env:LOCALAPPDATA = $TestLocalAppData
    $BeforeA = Get-Fingerprint
    $AssetsBeforeA = Get-AssetFingerprint
    Assert-NoSourceMediaLeak $AssetsBeforeA

    $RunA = Start-AppSnapshot $MainA "version-A"
    $FlowA = Start-FlowProbe $InstallDir "flow-A"
    $AfterA = Get-Fingerprint
    $AssetsA = Get-AssetFingerprint

    $b = Start-Process -FilePath $InstallerB -ArgumentList @("/S","/UPDATE",("/D="+$InstallDir)) -Wait -PassThru
    if ($b.ExitCode -ne 0) { throw "UPDATE_B_FAILED exit=$($b.ExitCode)" }
    $MainB = Get-MainExe $InstallDir
    $RunB = Start-AppSnapshot $MainB "version-B"
    $FlowB = Start-FlowProbe $InstallDir "flow-B"
    $AfterB = Get-Fingerprint
    $AssetsB = Get-AssetFingerprint

    $DbStable = $AfterA.fingerprint -eq $AfterB.fingerprint
    $AssetsStable = $AssetsA.fingerprint -eq $AssetsB.fingerprint
    $ProviderStable = (ConvertTo-Json $AfterA.providers -Compress -Depth 8) -eq (ConvertTo-Json $AfterB.providers -Compress -Depth 8)
    $ProjectsStable = (ConvertTo-Json $AfterA.projects -Compress -Depth 8) -eq (ConvertTo-Json $AfterB.projects -Compress -Depth 8)
    $SelectedStable = (ConvertTo-Json $AfterA.selected_media -Compress -Depth 8) -eq (ConvertTo-Json $AfterB.selected_media -Compress -Depth 8)
    $FinalsStable = (ConvertTo-Json $AfterA.finals -Compress -Depth 8) -eq (ConvertTo-Json $AfterB.finals -Compress -Depth 8)
    $FlowsStable = (ConvertTo-Json $AfterA.flow_sessions -Compress -Depth 8) -eq (ConvertTo-Json $AfterB.flow_sessions -Compress -Depth 8)
    $NarratorStable = (ConvertTo-Json $AssetsA.narrator_tts -Compress -Depth 8) -eq (ConvertTo-Json $AssetsB.narrator_tts -Compress -Depth 8)
    $SpeakerStable = ((ConvertTo-Json $AssetsA.speaker_calibration -Compress -Depth 8) -eq (ConvertTo-Json $AssetsB.speaker_calibration -Compress -Depth 8)) -and ((ConvertTo-Json $AssetsA.speaker_embeddings -Compress -Depth 8) -eq (ConvertTo-Json $AssetsB.speaker_embeddings -Compress -Depth 8))
    $FinalFilesOk = @($AssetsB.final_files | Where-Object { -not $_.file -or -not $_.file.exists }).Count -eq 0
    $SelectedFilesOk = @($AssetsB.selected_media_files | Where-Object { -not $_.file -or -not $_.file.exists }).Count -eq 0

    $Result = [ordered]@{
        ok = ($DbStable -and $AssetsStable -and $ProviderStable -and $ProjectsStable -and $SelectedStable -and $FinalsStable -and $FlowsStable -and $NarratorStable -and $SpeakerStable -and $FinalFilesOk -and $SelectedFilesOk -and $RunA.ready.ready -and $RunB.ready.ready -and $RunA.ready.checks.flow_authenticated -and $RunB.ready.checks.flow_authenticated)
        version_a_product=$RunA.product_version
        version_b_product=$RunB.product_version
        version_a_file=$RunA.file_version
        version_b_file=$RunB.file_version
        db_preserved=$DbStable
        provider_key_metadata_preserved=$ProviderStable
        projects_preserved=$ProjectsStable
        selected_media_preserved=$SelectedStable
        final_records_preserved=$FinalsStable
        flow_sessions_preserved=$FlowsStable
        asset_fingerprint_preserved=$AssetsStable
        narrator_tts_preserved=$NarratorStable
        speaker_calibration_preserved=$SpeakerStable
        selected_files_exist=$SelectedFilesOk
        final_files_exist=$FinalFilesOk
        flow_authenticated_a=[bool]$RunA.ready.checks.flow_authenticated
        flow_authenticated_b=[bool]$RunB.ready.checks.flow_authenticated
        project_count=@($AfterB.projects).Count
        selected_media_count=@($AfterB.selected_media).Count
        final_render_count=@($AfterB.finals).Count
        provider_count=@($AfterB.providers).Count
        narrator_file_count=@($AssetsB.narrator_tts).Count
        speaker_calibration_file_count=@($AssetsB.speaker_calibration).Count
        startup_a_ms=$RunA.startup_ms
        startup_b_ms=$RunB.startup_ms
        test_root=$RunRoot
    }
    $Result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    $Result | ConvertTo-Json -Depth 8
    if (-not $Result.ok) { exit 1 }
}
finally {
    $env:LOCALAPPDATA = $SourceLocalAppData
    if ($null -eq $PreviousToken) { Remove-Item Env:TH_MEDIA_AUTH_TOKEN -ErrorAction SilentlyContinue }
    else { $env:TH_MEDIA_AUTH_TOKEN = $PreviousToken }

    try {
        if (-not $Uninstaller) {
            $Uninstaller = Get-ChildItem $InstallDir -File -Filter "*uninstall*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        if ($Uninstaller -and (Test-Path $Uninstaller.FullName)) {
            Start-Process -FilePath $Uninstaller.FullName -ArgumentList @("/S") -Wait | Out-Null
        }
    } catch {}

    try {
        Remove-Item -LiteralPath $RealUpdates -Recurse -Force -ErrorAction SilentlyContinue
        if ($HadRealUpdates) { Copy-Tree (Join-Path $MetadataBackup "Updates") $RealUpdates }
        Remove-Item -LiteralPath $RealInstallerBackups -Recurse -Force -ErrorAction SilentlyContinue
        if ($HadRealInstallerBackups) { Copy-Tree (Join-Path $MetadataBackup "InstallerBackups") $RealInstallerBackups }
    } catch {}

    if ($Result -and $Result.ok) {
        Remove-Item -LiteralPath $RunRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
