param([Parameter(Mandatory=$true)][string]$TaskId)
$ErrorActionPreference='Stop'
$repo = Split-Path $PSScriptRoot -Parent
if ($TaskId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*$') { throw 'Invalid TASK ID' }
$status = Get-Content -LiteralPath (Join-Path $repo "tasks/$TaskId/visual_finish/status.json") -Raw | ConvertFrom-Json
$package = Split-Path $status.jsx -Parent
$log = Join-Path $package 'native_progress.log'
$statePath = Join-Path $package 'native_status.txt'
$seen=0
Write-Host 'Monitor only: waiting for the user to run JSX in Premiere.'
while ($true) {
    if (Test-Path -LiteralPath $log) {
        $lines=@(Get-Content -LiteralPath $log -Encoding utf8)
        for ($n=$seen; $n -lt $lines.Count; $n++) { Write-Host $lines[$n] }
        $seen=$lines.Count
    }
    if (Test-Path -LiteralPath $statePath) {
        $native=Get-Content -LiteralPath $statePath -Raw
        if ($native -match '^FAILED|^EXPORT_FILE_CREATED_REQUIRES_MEDIA_QA') { Write-Host $native; Write-Host 'STOP: send this result for verification.'; break }
    }
    Write-Host ('Monitor heartbeat '+(Get-Date -Format 'HH:mm:ss')+'; does not confirm native completion.')
    Start-Sleep -Seconds 5
}
