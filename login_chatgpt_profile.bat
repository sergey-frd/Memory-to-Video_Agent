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
    echo Chrome executable was not found. Edit CHROME_EXE in login_chatgpt_profile.bat.
    exit /b 1
)

set "PROFILE_DIR=%~dp0.browser-profile\chatgpt-web"
set "START_URL=https://chatgpt.com/"

echo Opening ChatGPT sign-in window for the automation profile...
echo 1. Sign in in the opened Chrome window.
echo 2. Verify that https://chatgpt.com/ opens and the prompt box is available.
echo 3. Close that Chrome window completely when you finish the check.
echo 4. Then run run_chatgpt_portrait_batch.bat.

start "" "%CHROME_EXE%" --new-window --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --disable-sync --user-data-dir="%PROFILE_DIR%" "%START_URL%"

exit /b 0
