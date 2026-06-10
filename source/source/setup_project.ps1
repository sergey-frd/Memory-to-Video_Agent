param(
    [string]$PythonExe = "python",
    [string]$VenvDir = ".venv",
    [string]$ConfigFile = "config.local.json",
    [switch]$InstallChromium,
    [switch]$ForceConfig
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message"
}

function Ensure-Directory {
    param([string]$PathValue)
    if (-not (Test-Path -LiteralPath $PathValue)) {
        New-Item -ItemType Directory -Path $PathValue | Out-Null
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Step "Project root: $projectRoot"

if (-not (Test-Path -LiteralPath $VenvDir)) {
    Write-Step "Creating virtual environment in $VenvDir"
    & $PythonExe -m venv $VenvDir
} else {
    Write-Step "Virtual environment already exists in $VenvDir"
}

$venvPython = Join-Path $projectRoot "$VenvDir\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment Python was not found: $venvPython"
}

Write-Step "Upgrading pip"
& $venvPython -m pip install --upgrade pip

Write-Step "Installing project dependencies"
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")

if ($InstallChromium) {
    Write-Step "Installing Playwright Chromium runtime"
    & $venvPython -m playwright install chromium
}

Write-Step "Ensuring local runtime directories"
$runtimeDirs = @(
    "input",
    "output",
    "error",
    "cleanup_archive",
    "final_project",
    "final_project\videos",
    "final_project\regeneration_assets",
    ".browser-profile",
    ".browser-profile\grok-web",
    ".browser-profile\chatgpt-web"
)
foreach ($dir in $runtimeDirs) {
    Ensure-Directory -PathValue (Join-Path $projectRoot $dir)
}

$envTemplatePath = Join-Path $projectRoot ".env.template"
$envTemplate = @"
# Fill these values and keep the real file only in .env
OPENAI_API_KEY=
XAI_API_KEY=
OPENAI_IMAGE_MODEL=gpt-image-1.5
OPENAI_PROMPT_MODEL=gpt-4.1-mini
OPENAI_SCENE_MODEL=gpt-4.1-mini
OPENAI_SCENE_REPAIR_MODEL=gpt-4.1-mini
OPENAI_MOTION_MODEL=gpt-4.1-mini
"@

Write-Step "Writing .env.template"
Set-Content -LiteralPath $envTemplatePath -Value $envTemplate -Encoding UTF8

$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Write-Step "Creating placeholder .env"
    Set-Content -LiteralPath $envPath -Value $envTemplate -Encoding UTF8
} else {
    Write-Step ".env already exists, leaving it unchanged"
}

$configTargetPath = Join-Path $projectRoot $ConfigFile
if ((-not (Test-Path -LiteralPath $configTargetPath)) -or $ForceConfig) {
    Write-Step "Writing $ConfigFile"
    $baseConfig = Get-Content -LiteralPath (Join-Path $projectRoot "config_BASE.json") -Raw | ConvertFrom-Json
    $config = [ordered]@{}
    foreach ($property in $baseConfig.PSObject.Properties) {
        $config[$property.Name] = $property.Value
    }
    $config["final_videos_dir"] = "final_project/videos"
    $config["regeneration_assets_dir"] = "final_project/regeneration_assets"
    $configJson = $config | ConvertTo-Json -Depth 10
    Set-Content -LiteralPath $configTargetPath -Value $configJson -Encoding UTF8
} else {
    Write-Step "$ConfigFile already exists, leaving it unchanged"
}

$chromeCandidates = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
)
$chromePath = $chromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

Write-Host ""
Write-Host "Setup completed."
Write-Host "Next steps:"
Write-Host "1. Fill API keys in .env"
Write-Host "2. Put source images into input\\"
Write-Host "3. Run .\\run_full_grok_pipeline_local.bat"
if ($chromePath) {
    Write-Host "Detected Chrome: $chromePath"
} else {
    Write-Host "Chrome was not detected automatically. Edit run_full_grok_pipeline_local.bat if needed."
}
