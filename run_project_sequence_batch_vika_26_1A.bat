@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
echo Running Vika batch in safe local mode.
echo Config: %SCRIPT_DIR%project_sequence_batch_vika_26_1A.json
echo Output project: %SCRIPT_DIR%output\Vika_26_1A_prproj_safe_build\Vika_26_1A_with_optimized_sequences_safe_local.prproj
echo Reports dir: %SCRIPT_DIR%output\Vika_26_1A_prproj_safe_build
echo Transition mode: recommend_only
echo Auto transitions: disabled ^(recommendations only^)
echo Transition handle trimming: disabled
echo Subject series grouping: disabled
echo.
call "%SCRIPT_DIR%run_project_sequence_batch.bat" "%SCRIPT_DIR%project_sequence_batch_vika_26_1A.json"
exit /b %ERRORLEVEL%
