@echo off
setlocal
cd /d "%~dp0"

set "CONFIG=%~1"
if not defined CONFIG set "CONFIG=hero_definition_Alice.json"

python main_hero_definition.py --config "%CONFIG%"
if errorlevel 1 (
  echo Hero definition failed.
  exit /b 1
)

echo Hero definition completed.
endlocal
