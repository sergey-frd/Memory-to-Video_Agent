@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    echo Usage: run_sequence_keep_apply.bat path\to\sequence_keep_apply_config.json
    echo.
    echo Examples:
    echo   run_sequence_keep_apply.bat sequence_keep_apply_yotam26_2_min.json
    echo   run_sequence_keep_apply.bat sequence_keep_apply_template.json
    echo   run_sequence_keep_apply.bat <LOCAL_PATH>
    echo.
    echo Template: sequence_keep_apply_template.json
    echo Shared launcher: run_sequence_trim_review.bat also accepts the same config
    echo when "mode" is apply_keep_ranges or keep_to_new_sequence.
    exit /b 1
)

python "%SCRIPT_DIR%main_sequence_trim_review.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Keep apply failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Keep apply completed successfully.
exit /b 0
