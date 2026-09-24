# Candidate resolution shared by the Windows release runtime staging script.
#
# PowerShell unrolls the output of a function that returns a single-element
# array back into a scalar string, so `$Paths[0]` would then yield the first
# character of the path instead of the path. Every selector here re-wraps its
# result with the comma operator so callers always receive a real array.

function Test-WhisperModelDir {
    [CmdletBinding()]
    param([string] $Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }

    return (Test-Path -LiteralPath (Join-Path $Path "model.bin") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Path "config.json") -PathType Leaf) -and
           (Test-Path -LiteralPath (Join-Path $Path "tokenizer.json") -PathType Leaf) -and
           (@(Get-ChildItem -LiteralPath $Path -File -Filter "vocabulary*" -ErrorAction SilentlyContinue).Count -gt 0)
}

function Select-ExistingFilePath {
    [CmdletBinding()]
    param([string[]] $Candidates)

    $found = @(
        foreach ($candidate in $Candidates) {
            if (-not [string]::IsNullOrWhiteSpace($candidate) -and
                (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                (Resolve-Path -LiteralPath $candidate).Path
            }
        }
    )
    return ,$found
}

function Select-WhisperModelDir {
    [CmdletBinding()]
    param([string[]] $Candidates)

    $found = @(
        foreach ($candidate in $Candidates) {
            if (Test-WhisperModelDir -Path $candidate) {
                (Resolve-Path -LiteralPath $candidate).Path
            }
        }
    )
    return ,$found
}
