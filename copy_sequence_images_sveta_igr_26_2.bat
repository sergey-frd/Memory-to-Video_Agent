@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"

set "PROJECT=e:\Git\video_projects\Sveta_I\Svt_Igr_26_2.prproj"
set "SEQUENCE=Svt_Igr_262_e01"
set "DEST=e:\Git\P_h_o_t_o\Igor_Brams_1\Igor_Brams\2026_Sv\input"
set "MANIFEST=%SCRIPT_DIR%output\Svt_Igr_26_2_e01_image_copy_manifest.json"

echo Project:     %PROJECT%
echo Sequence:    %SEQUENCE%
echo Destination: %DEST%
echo Manifest:    %MANIFEST%
echo.

call "%SCRIPT_DIR%run_copy_sequence_images.bat" --project "%PROJECT%" --sequence "%SEQUENCE%" --dest "%DEST%" --manifest "%MANIFEST%"
exit /b %ERRORLEVEL%
