@echo off
setlocal

cd /d "%~dp0"

set "GROK_DEBUG_PORT=9222"
set "GROK_REQUIRE_DEBUG_PORT=1"

echo Grok work-window attach-only runner: %~f0
echo This runner does not start Chrome. It only attaches to port %GROK_DEBUG_PORT%.

powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:%GROK_DEBUG_PORT%/json/version' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
if not "%ERRORLEVEL%"=="0" (
    echo Grok work-window Chrome is not listening on port %GROK_DEBUG_PORT%.
    echo Open the reusable Chrome debug window once, sign in to Grok if needed, leave that Chrome window open, then run this script again.
    exit /b 1
)

python ".\scripts\check_grok_work_window.py" %GROK_DEBUG_PORT%
if not "%ERRORLEVEL%"=="0" (
    exit /b %ERRORLEVEL%
)

python ".\main_full_pipeline.py" --config-file ".\config.json" --chrome-debug-port %GROK_DEBUG_PORT% --require-grok-debug-port --reuse-existing-grok-page %*
exit /b %errorlevel%
