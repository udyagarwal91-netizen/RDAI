@echo off
REM Double-click this file to start the agent on Windows.
REM First run takes a few minutes (it installs what the agent needs).
cd /d "%~dp0"
where py >nul 2>nul || (echo Python is not installed. See README.md, step 1. & pause & exit /b 1)
if not exist .venv (
  echo Setting up for the first time...
  py -3 -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
)
:start
.venv\Scripts\python -m pip install --quiet --disable-pip-version-check -r requirements.txt || (echo Install failed. Check your internet. & pause & exit /b 1)
.venv\Scripts\python run.py
REM Exit code 3 means "I just updated myself, start me again".
if %errorlevel%==3 (
  echo Restarting after update...
  goto start
)
pause
