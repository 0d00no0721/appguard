@echo off
rem One-click UAC helper: ask Windows to run install.bat elevated.
rem install.bat stops the normal guard, registers the logon task and starts it now.
setlocal
cd /d "%~dp0"
python "%~dp0elevate_install.py" %*
if errorlevel 1 pause
endlocal