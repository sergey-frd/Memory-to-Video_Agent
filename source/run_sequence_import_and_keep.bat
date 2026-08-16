@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    echo Usage: run_sequence_import_and_keep.bat path\to\sequence_import_and_keep_config.json
    echo.
    echo Examples:
    echo   run_sequence_import_and_keep.bat sequence_import_and_keep_template.json
    echo   run_sequence_import_and_keep.bat <LOCAL_PATH>
    echo.
    echo Runs import_media and apply_keep_ranges in one pass.
    echo Shared launcher: run_sequence_trim_review.bat also accepts the same config
    echo when "mode" is import_and_keep.
    exit /b 1
)

python "%SCRIPT_DIR%main_sequence_trim_review.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Import and keep failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Import and keep completed successfully.
exit /b 0
