@echo off
setlocal
chcp 65001 >nul
set "CLOSEOUT_STAGE=%~1"
set "CLOSEOUT_JOB=%~2"
set "CLOSEOUT_PLAN=%~3"
if "%CLOSEOUT_PLAN%"=="" goto usage
if "%CLOSEOUT_STAGE%"=="plan" goto plan
python "%~dp0tools\hero_video_closeout.py" "%CLOSEOUT_STAGE%" --plan "%CLOSEOUT_PLAN%" %4
exit /b %ERRORLEVEL%
:plan
python "%~dp0tools\hero_video_closeout.py" plan --job "%CLOSEOUT_JOB%" --plan "%CLOSEOUT_PLAN%"
exit /b %ERRORLEVEL%
:usage
echo Usage: %~nx0 plan^|archive^|verify^|cleanup^|publish-report job.json plan.json [--apply]
echo Cleanup without --apply only reports candidates. No AI calls.
exit /b 2
