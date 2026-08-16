@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    echo Usage: run_sequence_trim_review.bat path\to\sequence_trim_review_config.json
    echo Template: sequence_trim_review_template.json
    echo Keep-apply: run_sequence_keep_apply.bat sequence_keep_apply_yotam26_2_min.json
    echo Keep-apply template: sequence_keep_apply_template.json
    exit /b 1
)

python "%SCRIPT_DIR%main_sequence_trim_review.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Trim review failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Trim review completed successfully.
exit /b 0
