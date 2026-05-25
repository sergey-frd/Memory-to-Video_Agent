@echo off
setlocal

cd /d "%~dp0"

set "DEBUG_PORT=9222"

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:%DEBUG_PORT%/json/version' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
    python ".\scripts\check_grok_work_window.py" %DEBUG_PORT%
    if errorlevel 1 (
        echo Port %DEBUG_PORT% is already used by Chrome, but no Grok page is visible there.
        echo Close that stale/wrong Chrome debug process or open Grok in that exact reusable debug window, then run again.
        exit /b 2
    )
    echo Reusable AI work window is already listening on port %DEBUG_PORT%.
    echo Not opening another Chrome window.
    exit /b 0
)

set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE (
    for /f "delims=" %%P in ('where chrome.exe 2^>nul') do (
        if not defined CHROME_EXE set "CHROME_EXE=%%~fP"
    )
)

if not defined CHROME_EXE (
    echo Chrome executable was not found. Edit CHROME_EXE in open_ai_work_window.bat.
    exit /b 1
)

set "PROFILE_DIR=%~dp0.browser-profile\grok-web"

echo Opening one reusable AI work window for Grok and ChatGPT...
echo Leave this Chrome window open while the work-window batch scripts run.
echo Grok automation will connect to port %DEBUG_PORT%.

start "" "%CHROME_EXE%" --new-window --remote-debugging-port=%DEBUG_PORT% --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --disable-sync --user-data-dir="%PROFILE_DIR%" "https://grok.com/imagine" "https://chatgpt.com/"

exit /b 0
