@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    echo Usage: run_sequence_import_and_keep_standalone.bat path\to\sequence_import_and_keep_config.json
    echo.
    echo Portable alias of run_sequence_import_and_keep.bat.
    echo Calls main_premiere_import_keep.py with the same JSON.
    exit /b 1
)

python "%SCRIPT_DIR%main_premiere_import_keep.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Import and keep failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Import and keep completed successfully.
exit /b 0
