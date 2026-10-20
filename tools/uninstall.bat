@echo off
rem ============================================================
rem  AppGuard uninstaller  (ASCII-only messages on purpose)
rem  Run as Administrator.  Removes the logon task and stops
rem  the running guard.  AppGuard never modifies services or the
rem  registry, so there is nothing else to roll back.
rem ============================================================
setlocal
set "HERE=%~dp0"
set "TASK=AppGuard"

echo === AppGuard uninstall ===

net session >nul 2>&1
if errorlevel 1 (
  echo [x] Administrator rights required.
  exit /b 1
)

echo [1/3] Ask the running guard to exit ...
if exist "%HERE%guardctl.py" (
  python "%HERE%guardctl.py" stop
)

echo [2/3] Delete scheduled task "%TASK%" ...
schtasks /delete /tn "%TASK%" /f
if errorlevel 1 echo     (task was not registered - nothing to delete)

echo [3/3] Data dir kept at %ProgramData%\AppGuard
echo     Remove it manually if you want a clean slate:
echo         rmdir /s /q "%ProgramData%\AppGuard"
echo.
echo Done. AppGuard made no system-level changes (no service start-type
echo edits, no IFEO entries), so no rollback is required.
endlocal