@echo off
rem The connection to the VPS runs hidden in the background and starts at every Windows logon.
rem This file only makes sure it is running and opens the page. No window stays open.
if exist "%USERPROFILE%\.ui-dashboard\STOP" erase "%USERPROFILE%\.ui-dashboard\STOP" >nul 2>&1
wscript.exe "%USERPROFILE%\.ui-dashboard\run-hidden.vbs"
ping -n 4 127.0.0.1 >nul
start "" http://localhost:8899
