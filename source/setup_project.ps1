param(
    [string]$PythonExe = "python",
    [string]$VenvDir = ".venv",
    [string]$ConfigFile = "config.local.json",
    [switch]$Unlocked,
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

function Assert-NativeSuccess {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE). Setup stopped." }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

Write-Step "Project root: $projectRoot"

if (-not $Unlocked) {
    $pythonVersion = & $PythonExe -c "import platform; print(platform.python_version() + ' ' + platform.machine())"
    Assert-NativeSuccess "Python version check"
    if ($pythonVersion.Trim() -ne "3.14.2 AMD64") {
        throw "This release lock requires Python 3.14.2 AMD64; found $pythonVersion. Use -PythonExe with that interpreter. -Unlocked opts out of exact dependency reproduction."
    }
}

if (-not (Test-Path -LiteralPath $VenvDir)) {
    Write-Step "Creating virtual environment in $VenvDir"
    & $PythonExe -m venv $VenvDir
    Assert-NativeSuccess "Virtual environment creation"
} else {
    Write-Step "Virtual environment already exists in $VenvDir"
}

$venvRoot = if ([System.IO.Path]::IsPathRooted($VenvDir)) { $VenvDir } else { Join-Path $projectRoot $VenvDir }
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment Python was not found: $venvPython"
}

if (-not $Unlocked) {
    $venvVersion = & $venvPython -c "import platform; print(platform.python_version() + ' ' + platform.machine())"
    Assert-NativeSuccess "Existing environment version check"
    if ($venvVersion.Trim() -ne "3.14.2 AMD64") { throw "Existing venv uses $venvVersion. Choose a new -VenvDir; do not copy a venv between machines." }
}

Write-Step "Installing the release pip version"
& $venvPython -m pip install pip==26.0.1
Assert-NativeSuccess "pip installation"

Write-Step "Installing project dependencies"
$requirementsFile = if ($Unlocked) { "requirements.txt" } else { "requirements-lock-windows-py314.txt" }
& $venvPython -m pip install -r (Join-Path $projectRoot $requirementsFile)
Assert-NativeSuccess "Dependency installation"
& $venvPython -m pip check
Assert-NativeSuccess "Dependency consistency check"

if ($InstallChromium) {
    Write-Step "Installing Playwright Chromium runtime"
    & $venvPython -m playwright install chromium
    Assert-NativeSuccess "Chromium installation"
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

if (Test-Path -LiteralPath $envTemplatePath) {
    $envTemplate = Get-Content -LiteralPath $envTemplatePath -Raw
}

$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Write-Step "Creating placeholder .env"
    Set-Content -LiteralPath $envPath -Value $envTemplate -Encoding UTF8
} else {
    Write-Step ".env already exists, leaving it unchanged"
}

$configTargetPath = if ([System.IO.Path]::IsPathRooted($ConfigFile)) { $ConfigFile } else { Join-Path $projectRoot $ConfigFile }
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
if (-not $Unlocked) {
    & $venvPython (Join-Path $projectRoot "main_verify_installation.py")
    Assert-NativeSuccess "Release installation verification"
}
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
