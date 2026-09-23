param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$RuntimeRoot = Join-Path $ProjectRoot "desktop\runtime"
$BinDir = Join-Path $RuntimeRoot "bin"
$SpeakerDir = Join-Path $RuntimeRoot "models\speaker"
$SpeakerName = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
New-Item -ItemType Directory -Force -Path $SpeakerDir | Out-Null

function Resolve-Tool([string]$Name, [string]$EnvName) {
    $fromEnv = [Environment]::GetEnvironmentVariable($EnvName)
    if ($fromEnv -and (Test-Path $fromEnv -PathType Leaf)) {
        return (Resolve-Path $fromEnv).Path
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }
    throw "Missing required runtime tool: $Name. Set $EnvName or install it before build."
}
$Ffmpeg = Resolve-Tool "ffmpeg" "TH_MEDIA_FFMPEG_PATH"
$Ffprobe = Resolve-Tool "ffprobe" "TH_MEDIA_FFPROBE_PATH"

$SpeakerCandidates = @(
    [Environment]::GetEnvironmentVariable("FILM_SPEAKER_MODEL_PATH"),
    (Join-Path $env:LOCALAPPDATA "TH Media\Desktop\Models\speaker\$SpeakerName"),
    (Join-Path $ProjectRoot ".data\models\speaker\$SpeakerName")
) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) }

if (-not $SpeakerCandidates -or $SpeakerCandidates.Count -eq 0) {
    throw "Missing speaker ONNX model: $SpeakerName"
}
$SpeakerModel = (Resolve-Path $SpeakerCandidates[0]).Path

Copy-Item $Ffmpeg (Join-Path $BinDir "ffmpeg.exe") -Force
Copy-Item $Ffprobe (Join-Path $BinDir "ffprobe.exe") -Force
Copy-Item $SpeakerModel (Join-Path $SpeakerDir $SpeakerName) -Force

$StagedFfmpeg = Join-Path $BinDir "ffmpeg.exe"
$StagedFfprobe = Join-Path $BinDir "ffprobe.exe"
$StagedSpeaker = Join-Path $SpeakerDir $SpeakerName
$FfmpegVersion = & $StagedFfmpeg -version 2>&1
$FfmpegExit = $LASTEXITCODE
$FfmpegVersion | Select-Object -First 1 | Write-Output
if ($FfmpegExit -ne 0) {
    throw "Staged ffmpeg failed self-check with exit code $FfmpegExit."
}
$FfprobeVersion = & $StagedFfprobe -version 2>&1
$FfprobeExit = $LASTEXITCODE
$FfprobeVersion | Select-Object -First 1 | Write-Output
if ($FfprobeExit -ne 0) {
    throw "Staged ffprobe failed self-check with exit code $FfprobeExit."
}
if ((Get-Item $StagedSpeaker).Length -lt 1MB) {
    throw "Staged speaker model is unexpectedly small."
}

Write-Output "TH_MEDIA_RUNTIME_BIN_DIR=$BinDir"
Write-Output "TH_MEDIA_RUNTIME_MODEL_DIR=$(Join-Path $RuntimeRoot 'models')"
Write-Output "FILM_SPEAKER_MODEL_PATH=$StagedSpeaker"
