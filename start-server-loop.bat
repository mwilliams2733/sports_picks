@echo off
:loop
cd /d "C:\Users\mwill\OneDrive\Documents\mwilliams2733\sports_picks"
"C:\Users\mwill\AppData\Local\Programs\Python\Python314\python.exe" -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
echo Server exited, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
