@echo off
cd /d "%~dp0"
python -m pip install -r requirements-lock.txt
if errorlevel 1 (
  echo Could not install Python dependencies.
  pause
  exit /b 1
)
python run_web.py
pause

