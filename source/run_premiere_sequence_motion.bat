@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"
set "EXTRA_ARG=%~2"

if "%CONFIG_PATH%"=="" (
    echo Usage: run_premiere_sequence_motion.bat path\to\motion_config.json [--dry-run]
    echo.
    echo Supports mode=premiere_sequence_motion_animation and
    echo mode=premiere_sequence_insert_from_sequence_and_motion_animation through
    echo main_premiere_import_keep.py. Use --dry-run to write only the safe plan.
    exit /b 1
)

python "%SCRIPT_DIR%main_premiere_import_keep.py" --config "%CONFIG_PATH%" %EXTRA_ARG%
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Premiere sequence motion failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Premiere sequence motion completed successfully.
exit /b 0
