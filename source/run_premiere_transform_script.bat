@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if "%~1"=="" (
  echo Usage: run_premiere_transform_script.bat project_sequence_batch_config.json
  exit /b 1
)

set "CONFIG_PATH=%~1"
python "%SCRIPT_DIR%main_premiere_transform_script.py" --config "%CONFIG_PATH%"
