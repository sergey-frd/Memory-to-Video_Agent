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
    echo Chrome executable was not found. Edit CHROME_EXE in login_grok_profile.bat.
    exit /b 1
)

set "PROFILE_DIR=%~dp0.browser-profile\grok-web"
set "DEBUG_PORT=9222"
set "START_URL=https://grok.com/sign-in"

echo Opening Grok sign-in window for the automation profile...
echo 1. Sign in in the opened Chrome window.
echo 2. After successful sign-in, open https://grok.com/imagine once to verify access.
echo 3. Close that Chrome window completely when you finish the check.
echo 4. Then run run_full_grok_pipeline_local.bat in this clone.

start "" "%CHROME_EXE%" --new-window --remote-debugging-port=%DEBUG_PORT% --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --disable-sync --user-data-dir="%PROFILE_DIR%" "%START_URL%"

exit /b 0
