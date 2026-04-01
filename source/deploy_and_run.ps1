param(
    [string]$PythonExe = "python",
    [string]$VenvDir = ".venv",
    [string]$ConfigFile = "config.local.json",
    [string]$ChromeExe = "",
    [switch]$InstallChromium,
    [switch]$ForceConfig,
    [switch]$CheckOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PipelineArgs
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message"
}

function Get-DotEnvMap {
    param([string]$EnvPath)
    $map = @{}
    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return $map
    }
    foreach ($line in Get-Content -LiteralPath $EnvPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -ne 2) { continue }
        $map[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $map
}

function Find-Chrome {
    param([string]$ExplicitPath)
    if ($ExplicitPath) {
        if (Test-Path -LiteralPath $ExplicitPath) {
            return (Resolve-Path -LiteralPath $ExplicitPath).Path
        }
        throw "Chrome executable was not found: $ExplicitPath"
    }
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
    )
    $found = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    return $found
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Step "Running bootstrap"
& (Join-Path $projectRoot "setup_project.ps1") `
    -PythonExe $PythonExe `
    -VenvDir $VenvDir `
    -ConfigFile $ConfigFile `
    -InstallChromium:$InstallChromium `
    -ForceConfig:$ForceConfig

$venvPython = Join-Path $projectRoot "$VenvDir\Scripts\python.exe"
$configPath = Join-Path $projectRoot $ConfigFile
$envPath = Join-Path $projectRoot ".env"
$inputDir = Join-Path $projectRoot "input"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment Python was not found after bootstrap: $venvPython"
}
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Config file was not found after bootstrap: $configPath"
}

Write-Step "Checking .env"
$envMap = Get-DotEnvMap -EnvPath $envPath
$openAiKey = [string]($envMap["OPENAI_API_KEY"])
if (-not $openAiKey) {
    throw "OPENAI_API_KEY is missing in .env"
}

$xaiKey = [string]($envMap["XAI_API_KEY"])
if (-not $xaiKey) {
    Write-Host "Note: XAI_API_KEY is empty. This is acceptable for Grok Web mode, but required for xAI API video mode."
}

Write-Step "Checking Chrome"
$resolvedChrome = Find-Chrome -ExplicitPath $ChromeExe
if (-not $resolvedChrome) {
    throw "Chrome executable was not detected. Install Chrome or pass -ChromeExe <path>."
}

Write-Step "Checking input\\"
$supportedInputExtensions = @(".png", ".jpg", ".jpeg", ".webp", ".bmp")
$inputFiles = @()
if (Test-Path -LiteralPath $inputDir) {
    $inputFiles = Get-ChildItem -LiteralPath $inputDir -File | Where-Object { $supportedInputExtensions -contains $_.Extension.ToLowerInvariant() }
}
if (-not $inputFiles -or $inputFiles.Count -lt 1) {
    throw "No supported source images were found in input\\"
}

Write-Host ""
Write-Host "Environment is ready."
Write-Host "Config: $configPath"
Write-Host "Chrome: $resolvedChrome"
Write-Host "Input files: $($inputFiles.Count)"

if ($CheckOnly) {
    Write-Host "CheckOnly mode enabled. Pipeline was not started."
    exit 0
}

Write-Step "Starting local pipeline"
& $venvPython (Join-Path $projectRoot "main_full_pipeline.py") --config-file $configPath --chrome-exe $resolvedChrome @PipelineArgs
exit $LASTEXITCODE
