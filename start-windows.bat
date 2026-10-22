@echo off
REM Double-click this file to start the agent on Windows.
REM First run takes a few minutes (it installs what the agent needs).
cd /d "%~dp0"
where py >nul 2>nul || (echo Python is not installed. See README.md, step 1. & pause & exit /b 1)

REM The agent's libraries live in a short, fixed folder. Some library file names are very long, and
REM Windows refuses paths over 260 characters - a deep folder like Downloads\...\... would break the install.
set "VENV=%LOCALAPPDATA%\SkipperAgent\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo Setting up for the first time...
  py -3 -m venv "%VENV%" || (echo Could not set up Python. & pause & exit /b 1)
)
:start
"%VENV%\Scripts\python" -m pip install --quiet --disable-pip-version-check -r requirements.txt || (
  echo.
  echo Install failed. Check your internet connection, or send a photo of the red text above.
  pause
  exit /b 1
)
"%VENV%\Scripts\python" run.py
REM Exit code 3 means "I just updated myself, start me again".
if %errorlevel%==3 (
  echo Restarting after update...
  goto start
)
pause
