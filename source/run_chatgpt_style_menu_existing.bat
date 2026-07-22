@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "BASE_DIR=%~dp0"
set "LAUNCHER=%BASE_DIR%run_chatgpt_style_batch_existing.bat"
if not exist "%LAUNCHER%" (
  echo Launcher not found: "%LAUNCHER%"
  exit /b 1
)

set /a COUNT=0
for %%F in ("%BASE_DIR%chatgpt*_config.json") do (
  set /a COUNT+=1
  set "CFG_!COUNT!=%%~fF"
  set "NAME_!COUNT!=%%~nxF"
)

if %COUNT% EQU 0 (
  echo No style configs found by pattern: chatgpt*_config.json
  exit /b 1
)

echo.
echo Available style configs:
for /L %%I in (1,1,%COUNT%) do (
  echo   %%I^) !NAME_%%I!
)
echo.
set /p CHOICE=Select style number [1-%COUNT%] (Enter = 1): 
if "%CHOICE%"=="" set "CHOICE=1"
set "CHOICE=%CHOICE: =%"

for /f "delims=0123456789" %%A in ("%CHOICE%") do (
  echo Invalid choice: %CHOICE%
  exit /b 1
)

if %CHOICE% LSS 1 (
  echo Invalid choice: %CHOICE%
  exit /b 1
)
if %CHOICE% GTR %COUNT% (
  echo Invalid choice: %CHOICE%
  exit /b 1
)

set "SELECTED_CONFIG=!CFG_%CHOICE%!"
echo.
echo Selected config: "!SELECTED_CONFIG!"
echo.

set /p USE_DELIVERY=Use default delivery config config_Ziggi.json? [Y/n]: 
set "USE_DELIVERY=%USE_DELIVERY: =%"
if /I not "%USE_DELIVERY%"=="n" (
  set "DELIVERY_CFG=%BASE_DIR%config_Ziggi.json"
  if exist "!DELIVERY_CFG!" (
    echo Launching style batch with delivery config...
    call "%LAUNCHER%" "!SELECTED_CONFIG!" --delivery-config-file "!DELIVERY_CFG!"
    set "RC=%ERRORLEVEL%"
    echo Launcher exit code: !RC!
    exit /b !RC!
  )
  echo Default delivery config not found: "!DELIVERY_CFG!"
)

echo Launching style batch without delivery config...
call "%LAUNCHER%" "!SELECTED_CONFIG!"
set "RC=%ERRORLEVEL%"
echo Launcher exit code: !RC!
exit /b !RC!
