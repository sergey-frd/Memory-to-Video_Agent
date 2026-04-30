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
    echo Chrome executable was not found. Edit CHROME_EXE in login_chatgpt_debug_profile.bat.
    exit /b 1
)

set "PROFILE_DIR=%~dp0.browser-profile\chatgpt-web"
set "DEBUG_PORT=9333"
set "START_URL=https://chatgpt.com/"

echo Opening ChatGPT in a reusable Chrome debug session...
echo 1. Sign in and complete any human verification in this window.
echo 2. Leave this Chrome window open.
echo 3. In another terminal run: run_chatgpt_portrait_batch.bat --chrome-debug-port 9333 --skip-existing

start "" "%CHROME_EXE%" --new-window --remote-debugging-port=%DEBUG_PORT% --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --disable-sync --user-data-dir="%PROFILE_DIR%" "%START_URL%"

exit /b 0
