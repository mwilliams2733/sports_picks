@echo off
REM Wait for server to be ready
timeout /t 10 /nobreak >nul
:loop
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8000
echo Tunnel exited, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
