@echo off
setlocal

cd /d "%~dp0"

set "DEBUG_PORT=9222"
set "RESET_STALE_PORT=0"
set "RESTART_CHROME=0"
set "CHROME_PROFILE_DIRECTORY=%~1"
if /I "%~1"=="--reset-stale-port" (
    set "RESET_STALE_PORT=1"
    set "CHROME_PROFILE_DIRECTORY=%~2"
)
if /I "%~1"=="--restart-chrome" (
    set "RESTART_CHROME=1"
    set "RESET_STALE_PORT=1"
    set "CHROME_PROFILE_DIRECTORY=%~2"
)

echo Opening reusable AI work window in your normal Chrome profile.
echo This preserves your usual bookmarks, extensions, and logins.
echo.

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:%DEBUG_PORT%/json/version' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
    python ".\scripts\check_grok_work_window.py" %DEBUG_PORT%
    if errorlevel 1 (
        if "%RESET_STALE_PORT%"=="1" (
            for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%DEBUG_PORT% .*LISTENING"') do (
                echo Closing stale Chrome debug process on port %DEBUG_PORT%: PID %%P
                taskkill /F /T /PID %%P
            )
            powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>nul
            goto after_port_check
        )
        echo Port %DEBUG_PORT% is already used by Chrome, but no Grok page is visible there.
        echo Close that stale/wrong Chrome debug process or open Grok in that exact reusable debug window, then run again.
        echo Or run:
        echo   open_ai_work_window_user_chrome.bat --reset-stale-port
        exit /b 2
    )
    echo Reusable AI work window is already listening on port %DEBUG_PORT%.
    echo Not opening another Chrome window.
    exit /b 0
)

:after_port_check
powershell -NoProfile -Command "if (Get-Process chrome -ErrorAction SilentlyContinue) { exit 3 } else { exit 0 }" >nul 2>nul
if "%ERRORLEVEL%"=="3" (
    if "%RESTART_CHROME%"=="1" (
        echo Closing all Chrome processes so the normal profile can restart with debug port %DEBUG_PORT%.
        taskkill /F /IM chrome.exe /T >nul 2>nul
        powershell -NoProfile -Command "for ($i = 0; $i -lt 20; $i++) { if (-not (Get-Process chrome -ErrorAction SilentlyContinue)) { exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1" >nul 2>nul
        if errorlevel 1 (
            echo Chrome did not fully close. Close all Chrome windows manually and rerun this script.
            exit /b 3
        )
        goto start_chrome
    )
    echo Chrome is already running without the required debug port.
    echo Save your work, close all Chrome windows, then run this script first.
    echo After that, use this Chrome window normally while generation runs.
    echo Or, after saving work, run:
    echo   open_ai_work_window_user_chrome.bat --restart-chrome
    exit /b 3
)

:start_chrome
set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE (
    for /f "delims=" %%P in ('where chrome.exe 2^>nul') do (
        if not defined CHROME_EXE set "CHROME_EXE=%%~fP"
    )
)

if not defined CHROME_EXE (
    echo Chrome executable was not found. Edit CHROME_EXE in open_ai_work_window_user_chrome.bat.
    exit /b 1
)

echo Starting Chrome with remote debugging on port %DEBUG_PORT%.
echo Leave this Chrome window open while the work-window batch scripts run.

if defined CHROME_PROFILE_DIRECTORY (
    start "" "%CHROME_EXE%" --new-window --remote-debugging-port=%DEBUG_PORT% --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --profile-directory="%CHROME_PROFILE_DIRECTORY%" "https://grok.com/imagine" "https://chatgpt.com/"
) else (
    start "" "%CHROME_EXE%" --new-window --remote-debugging-port=%DEBUG_PORT% --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run "https://grok.com/imagine" "https://chatgpt.com/"
)

powershell -NoProfile -Command "Start-Sleep -Seconds 4" >nul 2>nul
python ".\scripts\check_grok_work_window.py" %DEBUG_PORT%
if errorlevel 1 (
    echo Chrome opened, but the debug port does not expose a Grok page yet.
    echo If Chrome restored another profile, close it and rerun with a profile directory, for example:
    echo   open_ai_work_window_user_chrome.bat "Default"
    echo   open_ai_work_window_user_chrome.bat "Profile 1"
    exit /b 2
)

exit /b 0
