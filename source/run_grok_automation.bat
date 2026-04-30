@echo off
setlocal

cd /d "%~dp0"

set "CHROME_EXE=<LOCAL_PATH> Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME_EXE%" (
    echo Chrome executable was not found. Edit CHROME_EXE in run_grok_automation.bat.
    exit /b 1
)

python ".\main_grok_web.py" --config-file ".\config.json" --chrome-exe "%CHROME_EXE%" --chrome-debug-port 9222 %*
exit /b %errorlevel%
