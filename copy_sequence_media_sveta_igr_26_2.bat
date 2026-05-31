@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
echo Running Sveta Igor media copy from config.
echo Config: %SCRIPT_DIR%copy_sequence_media_sveta_igr_26_2.json
echo Mode:   images only ^(set copy_videos=true in the config to also copy videos^)
echo.
call "%SCRIPT_DIR%run_copy_sequence_media_batch.bat" "%SCRIPT_DIR%copy_sequence_media_sveta_igr_26_2.json"
exit /b %ERRORLEVEL%
