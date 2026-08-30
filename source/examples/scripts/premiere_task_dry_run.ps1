<#
Examples only: fixed TASK contracts, not an arbitrary Premiere editor.
Reads the project and writes dry-run reports; never applies the edit.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('TASK_019', 'TASK_020_A', 'TASK_020_B', 'TASK_021', 'TASK_022', 'TASK_023', 'TASK_024', 'TASK_025', 'TASK_028')]
    [string]$Task,
    [Parameter(Mandatory = $true)]
    [string]$Config
)

$ErrorActionPreference = 'Stop'
$configPath = (Resolve-Path -LiteralPath $Config).Path
$entryPoints = @{
    TASK_019 = 'main_premiere_timeline_assembly.py'
    TASK_020_A = 'main_premiere_sequence_delete_only.py'
    TASK_020_B = 'main_premiere_sequence_coarse_insert.py'
    TASK_021 = 'main_premiere_sequence_ripple_delete.py'
    TASK_022 = 'main_premiere_sequence_insert_only.py'
    TASK_023 = 'main_premiere_sequence_replace_only.py'
    TASK_024 = 'main_premiere_short_core.py'
    TASK_025 = 'main_premiere_short_expansion.py'
    TASK_028 = 'main_premiere_task_028_dual_refinement.py'
}
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$cliArguments = @($entryPoints[$Task])
if ($Task -in @('TASK_019', 'TASK_020_A', 'TASK_020_B', 'TASK_021')) {
    $cliArguments += '--config'
}
$cliArguments += @($configPath, '--dry-run')
Push-Location -LiteralPath $repoRoot
try {
    & python @cliArguments
    if ($LASTEXITCODE -ne 0) { throw "Dry-run failed (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}
