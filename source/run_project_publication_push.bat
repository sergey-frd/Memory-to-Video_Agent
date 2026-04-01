@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "REPO_DIR=<LOCAL_PATH>
set "COMMIT_MESSAGE=Update project publication"

python ".\main_project_publication_push.py" --repo-dir "%REPO_DIR%" --commit-message "%COMMIT_MESSAGE%" --push %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Publication push failed with exit code %EXIT_CODE%.
    exit /b %EXIT_CODE%
)

echo.
echo Publication push completed successfully.
exit /b 0
