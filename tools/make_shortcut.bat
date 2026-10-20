@echo off
rem Create a desktop shortcut for AppGuard (pythonw tray launcher, appguard.ico).
setlocal
cd /d "%~dp0"
python "%~dp0make_shortcut.py" %*
if errorlevel 1 pause
endlocal