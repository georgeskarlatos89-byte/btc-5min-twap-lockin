@echo off
title Paper trading dashboard - keep this window open
echo.
echo   Paper trading dashboard
echo   -----------------------
echo   This window is the private connection to the VPS.
echo   Keep it open while you watch. Close it when you are done.
echo.
echo   Opening http://localhost:8899 in Chrome in 3 seconds...
echo.
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start chrome http://localhost:8899"
:loop
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 8899:127.0.0.1:8787 twapvm
echo.
echo   The connection dropped. Reconnecting in 5 seconds. Close this window to stop.
timeout /t 5 /nobreak >nul
goto loop
