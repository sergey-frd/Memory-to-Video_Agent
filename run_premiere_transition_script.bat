@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%~1"

if "%CONFIG_PATH%"=="" (
    set "CONFIG_PATH=%SCRIPT_DIR%project_sequence_batch_Ivan_26_1w_v03.json"
)

python "%SCRIPT_DIR%main_premiere_transition_script.py" --config "%CONFIG_PATH%"
