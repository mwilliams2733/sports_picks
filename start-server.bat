@echo off
cd /d "C:\Users\mwill\OneDrive\Documents\mwilliams2733\sports_picks"
"C:\Users\mwill\AppData\Local\Programs\Python\Python314\python.exe" -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
