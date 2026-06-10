@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    set "CONFIG_PATH=%SCRIPT_DIR%project_sequence_batch_vika_26_1A.json"
)

python "%SCRIPT_DIR%main_project_sequence_batch.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Batch optimization failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Batch optimization completed successfully.
exit /b 0
