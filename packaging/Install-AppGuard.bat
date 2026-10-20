@echo off
rem Workaround for UAC/Administrator-Protection temp redirection:
rem elevated setup may not be able to write the original user's %TEMP%.
rem Use the machine Temp directory, which the elevated setup can access.
setlocal
set "TEMP=%SystemRoot%\Temp"
set "TMP=%SystemRoot%\Temp"
"%~dp0AppGuardSetup-0.4.0-x64.exe" %*
endlocal