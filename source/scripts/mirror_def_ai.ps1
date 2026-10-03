[CmdletBinding()]
param(
    [ValidateSet('E-to-H', 'H-to-C', 'I-to-H')]
    [string]$Direction = 'E-to-H',
    [switch]$PreviewOnly
)

$ErrorActionPreference = 'Stop'

function Assert-PlainTree([string]$Path) {
    # Reject junctions/symlinks, including ancestors: never traverse outside the tree.
    $ancestor = $Path
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is not supported: $ancestor"
            }
        }
        $ancestor = Split-Path -Path $ancestor -Parent
    }
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($Path)
    while ($pending.Count -gt 0) {
        foreach ($item in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is not supported: $($item.FullName)"
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

try {
    $drives = switch ($Direction) {
        'E-to-H' { @('E', 'H') }
        'I-to-H' { @('I', 'H') }
        'H-to-C' {
            # Historical mode name; the external drive letter comes from this script.
            if ($PSScriptRoot -notmatch '^([A-Za-z]):\\Git\\AI_PIC_DEF\\def_AI\\img-style-ag_1\\scripts$') {
                throw 'Run the laptop launcher from the external project folder, not a copied standalone script.'
            }
            $externalDrive = $Matches[1].ToUpperInvariant()
            if ($externalDrive -eq 'C') {
                throw 'Run the laptop launcher from the external drive, not from C.'
            }
            @($externalDrive, 'C')
        }
    }
    $source = [IO.Path]::GetFullPath("$($drives[0]):\Git\AI_PIC_DEF\def_AI")
    $destination = [IO.Path]::GetFullPath("$($drives[1]):\Git\AI_PIC_DEF\def_AI")
    # Only the exact project subdirectory is eligible for deletion, never a drive root.
    foreach ($path in @($source, $destination)) {
        if ($path -notmatch '^[A-Za-z]:\\Git\\AI_PIC_DEF\\def_AI$') {
            throw "Unexpected project path: $path"
        }
        if (-not (Test-Path -LiteralPath ([IO.Path]::GetPathRoot($path)))) {
            throw "Drive is not connected: $path"
        }
    }
    if (-not (Test-Path -LiteralPath "$source\img-style-ag_1\AGENTS.md" -PathType Leaf)) {
        throw "Source project marker is missing: $source\img-style-ag_1\AGENTS.md"
    }
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        throw "Destination is a file: $destination"
    }
    $null = Get-Command robocopy.exe -ErrorAction Stop
    Write-Host "SOURCE      : $source"
    Write-Host "DESTINATION : $destination"
    Write-Host 'Source files are preserved. Extra destination files will be permanently deleted.'
    Write-Host 'Close editors, Premiere, browsers using project profiles, and running project jobs.'
    Write-Host 'Checking directory trees for unsafe links...'
    Assert-PlainTree $source
    Assert-PlainTree $destination

    $logDirectory = Join-Path $env:LOCALAPPDATA 'DefAI-Mirror-Logs'
    $null = New-Item -ItemType Directory -Force -Path $logDirectory
    $runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID
    # No exclusions: hidden files, .git, .env, media and environments are included.
    # Size and last-write time determine whether file data needs copying.
    $common = @('/COPY:DAT', '/DCOPY:DAT', '/IT', '/Z', '/R:2', '/W:2', '/XJ', '/SL', '/SJ', '/FP', '/BYTES', '/NP', '/TEE')
    function Invoke-MirrorPass([string]$Stage, [string[]]$Options) {
        $log = Join-Path $logDirectory "$runId-$Direction-$Stage.log"
        Write-Host "Log: $log"
        & robocopy.exe $source $destination @common @Options "/UNILOG:$log" | Out-Host
        $code = $LASTEXITCODE
        if ($code -ge 8) { throw "Robocopy failed ($code). See $log. Fix the problem and rerun." }
        return $code
    }

    $null = Invoke-MirrorPass 'preview' @('/MIR', '/L')
    Write-Host "Preview complete. Logs: $logDirectory"
    if ($PreviewOnly) { exit 0 }
    Write-Host "Apply $source -> $destination ?"
    $answer = Read-Host 'Type MIRROR to copy/update files and DELETE extra destination files; Enter cancels'
    if ($answer -cne 'MIRROR') { Write-Host 'Cancelled. Project files were not changed.'; exit 0 }

    # Repeat guards after confirmation. Copy successfully before starting any purge.
    if (-not (Test-Path -LiteralPath "$source\img-style-ag_1\AGENTS.md" -PathType Leaf)) {
        throw 'Source project disappeared. Stopping.'
    }
    Assert-PlainTree $source
    Assert-PlainTree $destination
    $null = Invoke-MirrorPass 'copy' @('/E')
    $null = Invoke-MirrorPass 'mirror' @('/MIR')
    $remaining = Invoke-MirrorPass 'verify' @('/MIR', '/L')
    if ($remaining -ne 0) {
        throw "Verification found remaining differences (code $remaining). Close active programs and rerun."
    }
    Write-Host 'SUCCESS: no remaining differences by robocopy size/time/attribute comparison.' -ForegroundColor Green
    Write-Host "Logs: $logDirectory"
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
