@echo off
rem ============================================================
rem  AppGuard installer  (messages are ASCII-only on purpose:
rem  .bat text encoding is unreliable across Windows locales)
rem  Run as Administrator. Registers a logon task AND starts it now.
rem ============================================================
setlocal
set "HERE=%~dp0"
set "TASK=AppGuard"

echo === AppGuard install ===

net session >nul 2>&1
if errorlevel 1 (
  echo [x] Administrator rights required ^(needed to register a highest-privilege task^).
  echo     Right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [x] python not found in PATH. Install Python 3.11+ first.
  pause
  exit /b 1
)

echo [1/5] Stop a running guard (if any) ...
python "%HERE%guardctl.py" stop >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/5] Init data dir and default config ...
python "%HERE%guardctl.py" init
if errorlevel 1 (
  echo [x] init failed.
  pause
  exit /b 1
)

echo [3/5] Resolve pythonw.exe ...
for /f "delims=" %%i in ('python -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
if not exist "%PYW%" set "PYW=python"

echo [4/5] Register logon task "%TASK%" (highest privileges, no console window) ...
schtasks /create /tn "%TASK%" /tr "\"%PYW%\" \"%HERE%guardctl.py\" serve --transport auto" /sc onlogon /rl highest /f
if errorlevel 1 (
  echo [x] Failed to register the scheduled task.
  pause
  exit /b 1
)

echo [5/5] Start the elevated guard now ...
schtasks /run /tn "%TASK%"
if errorlevel 1 (
  echo [x] Task registered but could not be started.
  echo     Try: schtasks /run /tn "%TASK%"
  pause
  exit /b 1
)

echo Waiting for the elevated guard ...
python "%HERE%wait_admin.py"
if errorlevel 1 (
  echo [x] The task started, but no elevated guard became reachable.
  echo     Check: %LOCALAPPDATA%\AppGuard\launcher.log
  echo     Check: schtasks /query /tn "%TASK%"
  pause
  exit /b 1
)

echo.
echo Done. The elevated guard is running now.
echo   Open the UI : double-click AppGuard.bat (project root)
echo   Real cleanup: Settings - turn off dry_run
echo   Stop guard  : python tools\guardctl.py stop
echo   Remove task : tools\uninstall.bat (as administrator)
echo.
pause
endlocal