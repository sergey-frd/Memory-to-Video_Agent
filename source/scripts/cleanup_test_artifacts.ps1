param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$names = @('.test_runs', '.pytest_cache', 'test_runtime', 'tmp', '__pycache__')
$targets = @(Get-ChildItem -LiteralPath $workspace -Directory -Force | Where-Object {
    $names -contains $_.Name -or $_.Name.StartsWith('pytest-cache-files-')
})
foreach ($folder in @('api','models','utils','tools','scripts','test','tests','services','source')) {
    $base = Join-Path $workspace $folder
    if (Test-Path -LiteralPath $base) {
        $targets += @(Get-ChildItem -LiteralPath $base -Directory -Recurse -Force | Where-Object Name -EQ '__pycache__')
    }
}
foreach ($item in $targets) {
    $path = [IO.Path]::GetFullPath($item.FullName)
    if (-not $path.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Outside workspace: $path"
    }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing link: $path" }
    $links = @(Get-ChildItem -LiteralPath $path -Recurse -Force | Where-Object {
        $_.Attributes -band [IO.FileAttributes]::ReparsePoint
    })
    if ($links.Count) { throw "Refusing tree containing links: $path" }
    $files = @(Get-ChildItem -LiteralPath $path -File -Recurse -Force)
    Write-Output "$path : $($files.Count) temporary files"
    if ($Apply) { Remove-Item -LiteralPath $path -Recurse -Force }
}
