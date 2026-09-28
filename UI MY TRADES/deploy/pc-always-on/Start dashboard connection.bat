@echo off
del "%~dp0STOP" >nul 2>&1
wscript.exe "%~dp0run-hidden.vbs"
echo The dashboard connection is starting in the background.
timeout /t 4 /nobreak >nul
start "" http://localhost:8899
