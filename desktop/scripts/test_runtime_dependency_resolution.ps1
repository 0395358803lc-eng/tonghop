param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "runtime_dependency_resolution.ps1")

$Workspace = Join-Path $env:TEMP ("th-media-candidates-" + [Guid]::NewGuid().ToString("N"))
$Results = New-Object System.Collections.Generic.List[object]

function Add-Result([string]$Name, [bool]$Passed, [string]$Detail) {
    $Results.Add([pscustomobject]@{ Name = $Name; Passed = $Passed; Detail = $Detail })
    if (-not $Quiet) {
        $marker = if ($Passed) { "PASS" } else { "FAIL" }
        Write-Output "[$marker] $Name$(if ($Detail) { " -> $Detail" })"
    }
}

function Assert-True([string]$Name, [bool]$Condition, [string]$Detail = "") {
    Add-Result $Name $Condition $Detail
}

try {
    New-Item -ItemType Directory -Force -Path $Workspace | Out-Null

    $speakerName = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
    $present = Join-Path $Workspace $speakerName
    $second = Join-Path $Workspace "second-$speakerName"
    Set-Content -LiteralPath $present -Value "model" -Encoding ascii
    Set-Content -LiteralPath $second -Value "model" -Encoding ascii
    $absent = Join-Path $Workspace "missing-$speakerName"

    # Regression for the release build failure: a single existing candidate must
    # survive as a real array, otherwise $paths[0] yields one character.
    $single = Select-ExistingFilePath -Candidates @($absent, $present, $null, "   ")
    Assert-True "single candidate stays an array" ($single -is [array]) ("type=" + $single.GetType().Name)
    Assert-True "single candidate count is 1" ($single.Count -eq 1) ("count=" + $single.Count)
    Assert-True "single candidate index 0 returns the full path" ($single[0] -eq (Resolve-Path -LiteralPath $present).Path) ("value=" + $single[0])

    $none = Select-ExistingFilePath -Candidates @($absent, "", $null)
    Assert-True "no candidate stays an array" ($none -is [array]) ("type=" + $none.GetType().Name)
    Assert-True "no candidate count is 0" ($none.Count -eq 0) ("count=" + $none.Count)

    $many = Select-ExistingFilePath -Candidates @($absent, $second, $present)
    Assert-True "multiple candidates keep declaration order" ($many.Count -eq 2 -and $many[0] -eq (Resolve-Path -LiteralPath $second).Path) ("first=" + $many[0])

    $scalarInput = Select-ExistingFilePath -Candidates $present
    Assert-True "scalar argument is promoted to an array" ($scalarInput -is [array] -and $scalarInput.Count -eq 1) ("type=" + $scalarInput.GetType().Name)

    $complete = Join-Path $Workspace "whisper-complete"
    New-Item -ItemType Directory -Force -Path $complete | Out-Null
    foreach ($file in @("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")) {
        Set-Content -LiteralPath (Join-Path $complete $file) -Value "x" -Encoding ascii
    }
    $noVocabulary = Join-Path $Workspace "whisper-no-vocabulary"
    New-Item -ItemType Directory -Force -Path $noVocabulary | Out-Null
    foreach ($file in @("model.bin", "config.json", "tokenizer.json")) {
        Set-Content -LiteralPath (Join-Path $noVocabulary $file) -Value "x" -Encoding ascii
    }
    $noTokenizer = Join-Path $Workspace "whisper-no-tokenizer"
    New-Item -ItemType Directory -Force -Path $noTokenizer | Out-Null
    foreach ($file in @("model.bin", "config.json", "vocabulary.txt")) {
        Set-Content -LiteralPath (Join-Path $noTokenizer $file) -Value "x" -Encoding ascii
    }
    $weightsOnly = Join-Path $Workspace "whisper-weights-only"
    New-Item -ItemType Directory -Force -Path $weightsOnly | Out-Null
    Set-Content -LiteralPath (Join-Path $weightsOnly "model.bin") -Value "x" -Encoding ascii

    Assert-True "complete Whisper dir accepted" (Test-WhisperModelDir -Path $complete)
    Assert-True "Whisper dir without vocabulary rejected" (-not (Test-WhisperModelDir -Path $noVocabulary))
    Assert-True "Whisper dir without tokenizer rejected" (-not (Test-WhisperModelDir -Path $noTokenizer))
    Assert-True "Whisper dir with only model.bin rejected" (-not (Test-WhisperModelDir -Path $weightsOnly))
    Assert-True "empty Whisper path rejected" (-not (Test-WhisperModelDir -Path ""))
    Assert-True "missing Whisper dir rejected" (-not (Test-WhisperModelDir -Path (Join-Path $Workspace "absent-dir")))

    $whisperSingle = Select-WhisperModelDir -Candidates @($weightsOnly, $noVocabulary, $complete)
    Assert-True "single Whisper candidate stays an array" ($whisperSingle -is [array]) ("type=" + $whisperSingle.GetType().Name)
    Assert-True "single Whisper candidate count is 1" ($whisperSingle.Count -eq 1) ("count=" + $whisperSingle.Count)
    Assert-True "single Whisper candidate index 0 returns the full path" ($whisperSingle[0] -eq (Resolve-Path -LiteralPath $complete).Path) ("value=" + $whisperSingle[0])

    $whisperNone = Select-WhisperModelDir -Candidates @($weightsOnly, $noTokenizer, $null)
    Assert-True "no Whisper candidate stays an array with count 0" ($whisperNone -is [array] -and $whisperNone.Count -eq 0) ("count=" + $whisperNone.Count)

    $failed = @($Results | Where-Object { -not $_.Passed })
    Write-Output "RUNTIME_CANDIDATE_REGRESSION_TESTS=$($Results.Count)"
    Write-Output "RUNTIME_CANDIDATE_REGRESSION_FAILED=$($failed.Count)"
    if ($failed.Count -gt 0) {
        Write-Output "RUNTIME_CANDIDATE_REGRESSION=FAIL"
        exit 1
    }
    Write-Output "RUNTIME_CANDIDATE_REGRESSION=PASS"
    exit 0
}
finally {
    if (Test-Path -LiteralPath $Workspace) {
        Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue
    }
}
