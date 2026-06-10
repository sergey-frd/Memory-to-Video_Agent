@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "REPO_DIR=<LOCAL_PATH>

python ".\main_project_publication_push.py" --repo-dir "%REPO_DIR%" --stage %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Publication stage failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Publication stage completed successfully.
exit /b 0
