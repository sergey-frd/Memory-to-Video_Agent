@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
call "%SCRIPT_DIR%run_project_sequence_batch.bat" "%SCRIPT_DIR%project_sequence_batch_nicol_26_T2.json"
exit /b %ERRORLEVEL%
