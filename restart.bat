@echo off
echo Rebuilding frontend...
cd /d "C:\Users\mwill\OneDrive\Documents\mwilliams2733\sports_picks\frontend"
call npm run build

echo Killing server...
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul

echo Starting server...
cd /d "C:\Users\mwill\OneDrive\Documents\mwilliams2733\sports_picks"
cscript //nologo start-server-hidden.vbs

echo Done! Server restarted. Tunnel stays running.
timeout /t 3 /nobreak >nul
