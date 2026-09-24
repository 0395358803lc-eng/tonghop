param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "runtime_dependency_resolution.ps1")
$RuntimeRoot = Join-Path $ProjectRoot "desktop\runtime"
$BinDir = Join-Path $RuntimeRoot "bin"
$SpeakerDir = Join-Path $RuntimeRoot "models\speaker"
$WhisperDir = Join-Path $RuntimeRoot "models\whisper\base"
$DownloadDir = Join-Path $RuntimeRoot ".downloads"

$SpeakerName = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
$SpeakerUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/$SpeakerName"
$SpeakerSha256 = "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
$WhisperRepo = "Systran/faster-whisper-base"

New-Item -ItemType Directory -Force -Path $BinDir,$SpeakerDir,$DownloadDir | Out-Null

function Resolve-Tool([string]$Name, [string]$EnvName) {
    $fromEnv = [Environment]::GetEnvironmentVariable($EnvName)
    if ($fromEnv -and (Test-Path $fromEnv -PathType Leaf)) {
        return (Resolve-Path $fromEnv).Path
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        $source = (Resolve-Path $command.Source).Path

        # Chocolatey exposes ffmpeg/ffprobe through tiny shim executables in
        # C:\ProgramData\chocolatey\bin. Copying a shim into TH Media runtime
        # breaks its relative target path, so resolve the real binary under lib.
        if ($source -match "\\chocolatey\\bin\\") {
            $chocoRoot = [Environment]::GetEnvironmentVariable("ChocolateyInstall")
            if (-not $chocoRoot) { $chocoRoot = "C:\ProgramData\chocolatey" }
            $chocoLib = Join-Path $chocoRoot "lib"
            if (Test-Path $chocoLib -PathType Container) {
                $actual = Get-ChildItem $chocoLib -Recurse -File -Filter "$Name.exe" -ErrorAction SilentlyContinue |
                    Where-Object { $_.FullName -notmatch "\\chocolatey\\bin\\" } |
                    Sort-Object LastWriteTime -Descending |
                    Select-Object -First 1
                if ($actual) {
                    Write-Host "Resolved Chocolatey $Name shim to $($actual.FullName)"
                    return $actual.FullName
                }
            }
        }

        return $source
    }
    throw "Missing required runtime tool: $Name. Set $EnvName or install it before build."
}

function Get-Sha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        } finally {
            $sha.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

$Ffmpeg = Resolve-Tool "ffmpeg" "TH_MEDIA_FFMPEG_PATH"
$Ffprobe = Resolve-Tool "ffprobe" "TH_MEDIA_FFPROBE_PATH"

$SpeakerCandidates = Select-ExistingFilePath -Candidates @(
    [Environment]::GetEnvironmentVariable("FILM_SPEAKER_MODEL_PATH"),
    (Join-Path $env:LOCALAPPDATA "TH Media\Desktop\Models\speaker\$SpeakerName"),
    (Join-Path $ProjectRoot ".data\models\speaker\$SpeakerName"),
    (Join-Path $SpeakerDir $SpeakerName)
)

if ($SpeakerCandidates.Count -eq 0) {
    $DownloadedSpeaker = Join-Path $DownloadDir $SpeakerName
    Write-Output "Downloading speaker model from official sherpa-onnx release..."
    Invoke-WebRequest -UseBasicParsing -Uri $SpeakerUrl -OutFile $DownloadedSpeaker
    $ActualHash = Get-Sha256 $DownloadedSpeaker
    if ($ActualHash -ne $SpeakerSha256) {
        Remove-Item -LiteralPath $DownloadedSpeaker -Force -ErrorAction SilentlyContinue
        throw "Speaker model SHA256 mismatch. Expected $SpeakerSha256, got $ActualHash."
    }
    $SpeakerCandidates = Select-ExistingFilePath -Candidates @($DownloadedSpeaker)
}
if ($SpeakerCandidates.Count -eq 0) {
    throw "No usable speaker model candidate; staging did not produce $SpeakerName."
}
$SpeakerModel = $SpeakerCandidates[0]

$HfSnapshotCandidates = @()
$HfSnapshotRoot = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--Systran--faster-whisper-base\snapshots"
if (Test-Path -LiteralPath $HfSnapshotRoot -PathType Container) {
    $HfSnapshotCandidates = @(Get-ChildItem -LiteralPath $HfSnapshotRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object { $_.FullName })
}

$WhisperCandidates = Select-WhisperModelDir -Candidates (
    @(
        [Environment]::GetEnvironmentVariable("TH_MEDIA_WHISPER_MODEL_DIR"),
        (Join-Path $env:LOCALAPPDATA "TH Media\Desktop\Models\whisper\base")
    ) + $HfSnapshotCandidates + @($WhisperDir)
)

if ($WhisperCandidates.Count -eq 0) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) { throw "Python is required during release build to download $WhisperRepo." }

    Write-Output "Downloading bundled faster-whisper base model..."
    $Downloader = Join-Path $DownloadDir "download_whisper_model.py"
    @'
from huggingface_hub import snapshot_download

path = snapshot_download(
    repo_id="Systran/faster-whisper-base",
    allow_patterns=[
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.*",
        "preprocessor_config.json",
    ],
)
print(path)
'@ | Set-Content -LiteralPath $Downloader -Encoding UTF8

    $DownloadOutput = & $Python.Source $Downloader
    $DownloadExit = $LASTEXITCODE
    Remove-Item -LiteralPath $Downloader -Force -ErrorAction SilentlyContinue
    if ($DownloadExit -ne 0) { throw "Failed to download $WhisperRepo." }
    $ResolvedDownload = ($DownloadOutput | Select-Object -Last 1).Trim()
    if (-not (Test-WhisperModelDir -Path $ResolvedDownload)) {
        throw "Downloaded Whisper model directory is incomplete: $ResolvedDownload"
    }
    $WhisperCandidates = Select-WhisperModelDir -Candidates @($ResolvedDownload)
}
if ($WhisperCandidates.Count -eq 0) {
    throw "No usable Whisper model candidate; staging did not produce a complete base model."
}
$WhisperSource = $WhisperCandidates[0]

Copy-Item -LiteralPath $Ffmpeg -Destination (Join-Path $BinDir "ffmpeg.exe") -Force
Copy-Item -LiteralPath $Ffprobe -Destination (Join-Path $BinDir "ffprobe.exe") -Force
$StagedSpeaker = Join-Path $SpeakerDir $SpeakerName
if ($SpeakerModel -ne $StagedSpeaker) {
    Copy-Item -LiteralPath $SpeakerModel -Destination $StagedSpeaker -Force
}

$ResolvedWhisperDest = $null
if (Test-Path -LiteralPath $WhisperDir -PathType Container) {
    $ResolvedWhisperDest = (Resolve-Path -LiteralPath $WhisperDir).Path
}
if ($ResolvedWhisperDest -ne $WhisperSource) {
    if (Test-Path -LiteralPath $WhisperDir) { Remove-Item -LiteralPath $WhisperDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $WhisperDir | Out-Null
    Copy-Item -Path (Join-Path $WhisperSource "*") -Destination $WhisperDir -Recurse -Force
}

# Last-resort browser for Google Flow, so a machine with neither Chrome nor
# Edge still works. Playwright is a pinned build dependency, so asking it for
# its own Chromium keeps the browser and the driver that launches it in step.
$BrowserDir = Join-Path $RuntimeRoot "browser\chromium"
$BundledBrowser = Join-Path $BrowserDir "chrome.exe"
if (-not (Test-Path -LiteralPath $BundledBrowser -PathType Leaf)) {
    $BrowserPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $BrowserPython) { throw "Python is required during release build to stage the bundled Chromium." }

    Write-Output "Staging bundled Chromium through the pinned Playwright runtime..."
    $BrowserStage = Join-Path $DownloadDir "playwright-browsers"
    New-Item -ItemType Directory -Force -Path $BrowserStage | Out-Null
    $PreviousBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
    $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserStage
    try {
        & $BrowserPython.Source -m playwright install chromium --no-shell
        $BrowserExit = $LASTEXITCODE
    } finally {
        if ($null -eq $PreviousBrowsersPath) { Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue }
        else { $env:PLAYWRIGHT_BROWSERS_PATH = $PreviousBrowsersPath }
    }
    if ($BrowserExit -ne 0) { throw "Bundled Chromium download failed with exit code $BrowserExit." }

    $ChromiumBuild = @(Get-ChildItem -LiteralPath $BrowserStage -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "chrome-win64\chrome.exe") -PathType Leaf } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1)
    if ($ChromiumBuild.Count -eq 0) {
        throw "Playwright produced no chromium-*/chrome-win64 build under $BrowserStage."
    }
    New-Item -ItemType Directory -Force -Path $BrowserDir | Out-Null
    Copy-Item -Path (Join-Path $ChromiumBuild[0].FullName "chrome-win64\*") -Destination $BrowserDir -Recurse -Force
}
if (-not (Test-Path -LiteralPath $BundledBrowser -PathType Leaf)) {
    throw "Bundled Chromium is missing from the release runtime: $BundledBrowser"
}

$StagedFfmpeg = Join-Path $BinDir "ffmpeg.exe"
$StagedFfprobe = Join-Path $BinDir "ffprobe.exe"

$FfmpegVersion = & $StagedFfmpeg -version 2>&1
$FfmpegExit = $LASTEXITCODE
$FfmpegVersion | Select-Object -First 1 | Write-Output
if ($FfmpegExit -ne 0) { throw "Staged ffmpeg failed self-check with exit code $FfmpegExit." }

$FfprobeVersion = & $StagedFfprobe -version 2>&1
$FfprobeExit = $LASTEXITCODE
$FfprobeVersion | Select-Object -First 1 | Write-Output
if ($FfprobeExit -ne 0) { throw "Staged ffprobe failed self-check with exit code $FfprobeExit." }

if ((Get-Item $StagedSpeaker).Length -lt 1MB) { throw "Staged speaker model is unexpectedly small." }
$SpeakerHash = Get-Sha256 $StagedSpeaker
if ($SpeakerHash -ne $SpeakerSha256) { throw "Staged speaker model hash mismatch." }
if (-not (Test-WhisperModelDir -Path $WhisperDir)) { throw "Staged faster-whisper base model is incomplete." }
if ((Get-Item -LiteralPath (Join-Path $WhisperDir "model.bin")).Length -lt 50MB) { throw "Staged Whisper model.bin is unexpectedly small." }

# chrome.exe is a GUI-subsystem binary: PowerShell does not attach to its stdout,
# so the staged tree is validated structurally here and actually executed by the
# installed-package smoke, which waits for the process and captures its output.
foreach ($BrowserPart in @("chrome.exe", "chrome.dll")) {
    if (-not (Test-Path -LiteralPath (Join-Path $BrowserDir $BrowserPart) -PathType Leaf)) {
        throw "Bundled Chromium is incomplete: missing $BrowserPart"
    }
}
$BrowserVersionDirs = @(Get-ChildItem -LiteralPath $BrowserDir -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d+(\.\d+){3}' })
if ($BrowserVersionDirs.Count -eq 0) { throw "Bundled Chromium has no versioned manifest directory." }
$BrowserBytes = (Get-ChildItem -LiteralPath $BrowserDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
if ($BrowserBytes -lt 250MB) { throw "Bundled Chromium looks incomplete: only $([math]::Round($BrowserBytes / 1MB, 1)) MB staged." }
Write-Output "BUNDLED_CHROME=$($BrowserVersionDirs[0].Name) sizeMB=$([math]::Round($BrowserBytes / 1MB, 1))"

Write-Output "TH_MEDIA_RUNTIME_BIN_DIR=$BinDir"
Write-Output "TH_MEDIA_RUNTIME_MODEL_DIR=$(Join-Path $RuntimeRoot 'models')"
Write-Output "FILM_SPEAKER_MODEL_PATH=$StagedSpeaker"
Write-Output "TH_MEDIA_WHISPER_MODEL_DIR=$WhisperDir"
Write-Output "TH_MEDIA_BUNDLED_BROWSER_PATH=$BundledBrowser"
Write-Output "WHISPER_MODEL_BUNDLED=PASS"
