@echo off
echo Stopping the dashboard connection...
echo stop> "%~dp0STOP"
for /f "tokens=2 delims=," %%p in ('wmic process where "name='ssh.exe' and commandline like '%%8899:127.0.0.1:8787%%'" get processid /format:csv ^| findstr /r "[0-9]"') do taskkill /PID %%p /F >nul 2>&1
echo Done. It will NOT start again until you run "Start dashboard connection.bat" or log in again after deleting the STOP file.
pause
