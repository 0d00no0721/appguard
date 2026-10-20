@echo off
rem AppGuard single entry: start the tray launcher (hidden console).
setlocal
cd /d "%~dp0"
set "PYW="
for /f "delims=" %%i in ('python -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"
if not exist "%PYW%" set "PYW=pythonw.exe"
start "" "%PYW%" "%~dp0tools\appguard_launcher.py" %*
endlocal