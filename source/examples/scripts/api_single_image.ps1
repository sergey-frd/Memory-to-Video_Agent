<#
Defaults to displaying arguments only: no API, project or queue changes.
Use -Run only after reviewing config, delivery paths and cleanup rules.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Image,
    [string]$Config = (Join-Path $PSScriptRoot '..\config_api_single_image.example.json'),
    [switch]$Run
)

$ErrorActionPreference = 'Stop'
$imagePath = (Resolve-Path -LiteralPath $Image).Path
$configPath = (Resolve-Path -LiteralPath $Config).Path
if (-not (Test-Path -LiteralPath $imagePath -PathType Leaf)) { throw 'Image must be a file.' }
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Config must be a file.' }
$null = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$cliArguments = @('main_full_pipeline_api.py', '--config-file', $configPath,
    '--image', $imagePath, '--single-image', '--result-timeout', '900')
if (-not $Run) {
    Write-Output 'Preview only. Review each argument below; -Run starts generation and normal cleanup.'
    Write-Output 'python'
    $cliArguments | ForEach-Object { Write-Output $_ }
    return
}
Push-Location -LiteralPath $repoRoot
try {
    & python @cliArguments
    if ($LASTEXITCODE -ne 0) { throw "API pipeline failed (exit $LASTEXITCODE)." }
} finally {
    Pop-Location
}
