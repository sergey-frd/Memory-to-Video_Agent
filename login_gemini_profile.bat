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
    echo Chrome executable was not found. Edit CHROME_EXE in login_gemini_profile.bat.
    exit /b 1
)

set "PROFILE_DIR=%~dp0.browser-profile\gemini-web"
set "START_URL=https://gemini.google.com/app"

echo Opening Gemini sign-in window for the automation profile...
echo 1. Sign in in the opened Chrome window.
echo 2. Verify that https://gemini.google.com/app opens and the prompt box is available.
echo 3. Keep this as a separate Gemini generation window with one visible tab.
echo 4. Then run run_gemini_portrait_batch_existing.bat.

start "" "%CHROME_EXE%" --new-window --disable-background-mode --disable-hang-monitor --hide-crash-restore-bubble --no-first-run --disable-sync --user-data-dir="%PROFILE_DIR%" "%START_URL%"

exit /b 0
