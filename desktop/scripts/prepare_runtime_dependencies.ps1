param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
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
                    Write-Output "Resolved Chocolatey $Name shim to $($actual.FullName)"
                    return $actual.FullName
                }
            }
        }

        return $source
    }
    throw "Missing required runtime tool: $Name. Set $EnvName or install it before build."
}

function Test-WhisperModelDir([string]$Path) {
    if (-not $Path -or -not (Test-Path $Path -PathType Container)) { return $false }
    return (Test-Path (Join-Path $Path "model.bin") -PathType Leaf) -and
           (Test-Path (Join-Path $Path "config.json") -PathType Leaf)
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

$SpeakerCandidates = @(
    [Environment]::GetEnvironmentVariable("FILM_SPEAKER_MODEL_PATH"),
    (Join-Path $env:LOCALAPPDATA "TH Media\Desktop\Models\speaker\$SpeakerName"),
    (Join-Path $ProjectRoot ".data\models\speaker\$SpeakerName"),
    (Join-Path $SpeakerDir $SpeakerName)
) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) }

if (-not $SpeakerCandidates -or $SpeakerCandidates.Count -eq 0) {
    $DownloadedSpeaker = Join-Path $DownloadDir $SpeakerName
    Write-Output "Downloading speaker model from official sherpa-onnx release..."
    Invoke-WebRequest -UseBasicParsing -Uri $SpeakerUrl -OutFile $DownloadedSpeaker
    $ActualHash = Get-Sha256 $DownloadedSpeaker
    if ($ActualHash -ne $SpeakerSha256) {
        Remove-Item -LiteralPath $DownloadedSpeaker -Force -ErrorAction SilentlyContinue
        throw "Speaker model SHA256 mismatch. Expected $SpeakerSha256, got $ActualHash."
    }
    $SpeakerCandidates = @($DownloadedSpeaker)
}
$SpeakerModel = (Resolve-Path $SpeakerCandidates[0]).Path

$WhisperCandidates = New-Object System.Collections.Generic.List[string]
$ExplicitWhisper = [Environment]::GetEnvironmentVariable("TH_MEDIA_WHISPER_MODEL_DIR")
if (Test-WhisperModelDir $ExplicitWhisper) { $WhisperCandidates.Add((Resolve-Path $ExplicitWhisper).Path) }

$DataWhisper = Join-Path $env:LOCALAPPDATA "TH Media\Desktop\Models\whisper\base"
if (Test-WhisperModelDir $DataWhisper) { $WhisperCandidates.Add((Resolve-Path $DataWhisper).Path) }

$HfSnapshotRoot = Join-Path $env:USERPROFILE ".cache\huggingface\hub\models--Systran--faster-whisper-base\snapshots"
if (Test-Path $HfSnapshotRoot -PathType Container) {
    Get-ChildItem $HfSnapshotRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object {
            if (Test-WhisperModelDir $_.FullName) { $WhisperCandidates.Add($_.FullName) }
        }
}
if (Test-WhisperModelDir $WhisperDir) { $WhisperCandidates.Add((Resolve-Path $WhisperDir).Path) }

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
    if (-not (Test-WhisperModelDir $ResolvedDownload)) {
        throw "Downloaded Whisper model directory is incomplete: $ResolvedDownload"
    }
    $WhisperCandidates.Add((Resolve-Path $ResolvedDownload).Path)
}

$WhisperSource = $WhisperCandidates[0]

Copy-Item $Ffmpeg (Join-Path $BinDir "ffmpeg.exe") -Force
Copy-Item $Ffprobe (Join-Path $BinDir "ffprobe.exe") -Force
$StagedSpeaker = Join-Path $SpeakerDir $SpeakerName
if ((Resolve-Path $SpeakerModel).Path -ne (Resolve-Path $SpeakerDir).Path + "\$SpeakerName") {
    Copy-Item $SpeakerModel $StagedSpeaker -Force
}

$ResolvedWhisperDest = $null
if (Test-Path $WhisperDir -PathType Container) {
    try { $ResolvedWhisperDest = (Resolve-Path $WhisperDir).Path } catch {}
}
if (-not $ResolvedWhisperDest -or $ResolvedWhisperDest -ne (Resolve-Path $WhisperSource).Path) {
    if (Test-Path $WhisperDir) { Remove-Item $WhisperDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $WhisperDir | Out-Null
    Copy-Item (Join-Path $WhisperSource "*") $WhisperDir -Recurse -Force
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
if (-not (Test-WhisperModelDir $WhisperDir)) { throw "Staged faster-whisper base model is incomplete." }
if ((Get-Item (Join-Path $WhisperDir "model.bin")).Length -lt 50MB) { throw "Staged Whisper model.bin is unexpectedly small." }

Write-Output "TH_MEDIA_RUNTIME_BIN_DIR=$BinDir"
Write-Output "TH_MEDIA_RUNTIME_MODEL_DIR=$(Join-Path $RuntimeRoot 'models')"
Write-Output "FILM_SPEAKER_MODEL_PATH=$StagedSpeaker"
Write-Output "TH_MEDIA_WHISPER_MODEL_DIR=$WhisperDir"
Write-Output "WHISPER_MODEL_BUNDLED=PASS"
