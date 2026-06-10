@echo off
setlocal

cd /d "%~dp0"

set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE (
    for /f "delims=" %%P in ('where chrome.exe 2^>nul') do (
        if not defined CHROME_EXE set "CHROME_EXE=%%~fP"
    )
)

if not defined CHROME_EXE (
    echo Chrome executable was not found. Edit CHROME_EXE in run_full_grok_pipeline_local.bat.
    exit /b 1
)

if not exist ".\.venv\Scripts\python.exe" (
    echo Local virtual environment was not found. Run setup_project.ps1 first.
    exit /b 1
)

if not exist ".\config.local.json" (
    echo config.local.json was not found. Run setup_project.ps1 first.
    exit /b 1
)

".\.venv\Scripts\python.exe" ".\main_full_pipeline.py" --config-file ".\config.local.json" --profile-dir ".\.browser-profile\grok-web" --chrome-exe "%CHROME_EXE%" %*
exit /b %errorlevel%
