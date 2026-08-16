@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" set "CONFIG_PATH=%SCRIPT_DIR%sequence_music_recommendation_Alice.json"

python -u "%SCRIPT_DIR%main_sequence_music_first.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Music recommendation failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Music recommendation completed successfully.
exit /b 0
