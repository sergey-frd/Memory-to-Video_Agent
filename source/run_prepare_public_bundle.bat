@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PUBLIC_REVIEW_DIR=%~1"
if "%PUBLIC_REVIEW_DIR%"=="" set "PUBLIC_REVIEW_DIR=project_publication\public_review"
if exist "%PUBLIC_REVIEW_DIR%" (
    echo Choose a new empty target path. Existing folders are not overwritten.
    exit /b 2
)
python -B "%~dp0main_project_publication.py" --source-root "%~dp0." --target-dir "%PUBLIC_REVIEW_DIR%"
if errorlevel 1 exit /b 1
python -B "%~dp0tools\audit_public_bundle.py" "%PUBLIC_REVIEW_DIR%"
exit /b %ERRORLEVEL%
