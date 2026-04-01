@echo off
setlocal

cd /d "%~dp0"

set "CHROME_EXE=<LOCAL_PATH> Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME_EXE%" (
    echo Chrome executable was not found. Edit CHROME_EXE in run_full_grok_pipeline.bat.
    exit /b 1
)

python ".\main_full_pipeline.py" --config-file ".\config.json" --chrome-exe "%CHROME_EXE%" %*
exit /b %errorlevel%
