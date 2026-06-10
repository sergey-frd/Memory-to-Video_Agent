$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$Source = Resolve-Path (Join-Path $RepoRoot "premiere_extensions\run_transition_script_panel")
$ExtensionsRoot = Join-Path $env:APPDATA "Adobe\CEP\extensions"
$Target = Join-Path $ExtensionsRoot "com.memorytovideo.transitionrunner"

New-Item -ItemType Directory -Force -Path $ExtensionsRoot | Out-Null
$ResolvedExtensionsRoot = (Resolve-Path $ExtensionsRoot).Path
$TargetFullPath = [System.IO.Path]::GetFullPath($Target)

if (-not $TargetFullPath.StartsWith($ResolvedExtensionsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside Adobe CEP extensions root: $TargetFullPath"
}

if (Test-Path -LiteralPath $TargetFullPath) {
    Remove-Item -LiteralPath $TargetFullPath -Recurse -Force
}

Copy-Item -LiteralPath $Source.Path -Destination $TargetFullPath -Recurse

foreach ($version in 9..13) {
    $key = "HKCU:\Software\Adobe\CSXS.$version"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name "PlayerDebugMode" -Value "1" -PropertyType String -Force | Out-Null
}

Write-Host "Installed Run Transition Script panel:"
Write-Host "  $TargetFullPath"
Write-Host ""
Write-Host "Restart Premiere Pro, then open Window > Extensions > Run Transition Script."
