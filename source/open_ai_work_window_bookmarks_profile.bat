@echo off
setlocal

cd /d "%~dp0"

set "DEBUG_PORT=9222"
set "SOURCE_PROFILE_DIRECTORY=%~1"
if not defined SOURCE_PROFILE_DIRECTORY set "SOURCE_PROFILE_DIRECTORY=Default"

set "CHROME_USER_DATA=%LOCALAPPDATA%\Google\Chrome\User Data"
set "SOURCE_PROFILE_DIR=%CHROME_USER_DATA%\%SOURCE_PROFILE_DIRECTORY%"
set "WORK_USER_DATA_DIR=%~dp0.browser-profile\chrome-bookmarks-work"
set "WORK_PROFILE_DIR=%WORK_USER_DATA_DIR%\Default"

echo Opening reusable AI work window with copied Chrome bookmarks.
echo Source Chrome profile: %SOURCE_PROFILE_DIRECTORY%
echo Work profile: %WORK_USER_DATA_DIR%
echo.

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:%DEBUG_PORT%/json/version' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
    python ".\scripts\check_grok_work_window.py" %DEBUG_PORT%
    if errorlevel 1 (
        echo Port %DEBUG_PORT% is already used by Chrome, but no Grok page is visible there.
        echo Close that stale/wrong Chrome debug process or run:
        echo   open_ai_work_window_user_chrome.bat --reset-stale-port
        exit /b 2
    )
    echo Reusable AI work window is already listening on port %DEBUG_PORT%.
    echo Not opening another Chrome window.
    exit /b 0
)

if not exist "%SOURCE_PROFILE_DIR%" (
    echo Source Chrome profile folder was not found:
    echo   %SOURCE_PROFILE_DIR%
    echo Try another profile name, for example:
    echo   open_ai_work_window_bookmarks_profile.bat "Default"
    echo   open_ai_work_window_bookmarks_profile.bat "Profile 1"
    exit /b 1
)

if not exist "%WORK_PROFILE_DIR%" mkdir "%WORK_PROFILE_DIR%"
if exist "%SOURCE_PROFILE_DIR%\Bookmarks" copy /Y "%SOURCE_PROFILE_DIR%\Bookmarks" "%WORK_PROFILE_DIR%\Bookmarks" >nul
if exist "%SOURCE_PROFILE_DIR%\Bookmarks.bak" copy /Y "%SOURCE_PROFILE_DIR%\Bookmarks.bak" "%WORK_PROFILE_DIR%\Bookmarks.bak" >nul

set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE (
    for /f "delims=" %%P in ('where chrome.exe 2^>nul') do (
        if not defined CHROME_EXE set "CHROME_EXE=%%~fP"
    )
)

if not defined CHROME_EXE (
    echo Chrome executable was not found. Edit CHROME_EXE in open_ai_work_window_bookmarks_profile.bat.
    exit /b 1
)

echo Starting Chrome work profile with remote debugging on port %DEBUG_PORT%.
echo Your bookmarks were copied into this work profile. Sign in to Grok/ChatGPT here once if needed.

start "" "%CHROME_EXE%" --new-window --remote-debugging-port=%DEBUG_PORT% --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --disable-sync --user-data-dir="%WORK_USER_DATA_DIR%" --profile-directory="Default" "https://grok.com/imagine" "https://chatgpt.com/"

powershell -NoProfile -Command "Start-Sleep -Seconds 6" >nul 2>nul
python ".\scripts\check_grok_work_window.py" %DEBUG_PORT%
if errorlevel 1 (
    echo Chrome opened, but the debug port does not expose a Grok page yet.
    echo Check whether another process is blocking port %DEBUG_PORT%, then retry.
    exit /b 2
)

exit /b 0
