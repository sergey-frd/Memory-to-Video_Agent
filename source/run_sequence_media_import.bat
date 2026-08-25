@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    echo Usage: run_sequence_media_import.bat path\to\sequence_media_import_config.json
    echo.
    echo Examples:
    echo   run_sequence_media_import.bat sequence_media_import_yotam26_part2.json
    echo   run_sequence_media_import.bat sequence_media_import_template.json
    echo   run_sequence_media_import.bat <LOCAL_PATH>
    echo.
    echo The import JSON itself can also be passed when it contains
    echo project_path, sequence_name, root_directory, and files.
    echo Dedicated runner: main_premiere_import_keep.py
    echo Portable alias: run_sequence_media_import_standalone.bat
    exit /b 1
)

python "%SCRIPT_DIR%main_premiere_import_keep.py" --config "%CONFIG_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Media import failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Media import completed successfully.
exit /b 0
