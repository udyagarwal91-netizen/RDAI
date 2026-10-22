@echo off
REM Double-click to start the RDAI stock app on Windows. First run installs everything.
cd /d %~dp0
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
if not exist data\rdai.db .venv\Scripts\python -m rdai.seed
.venv\Scripts\streamlit run app.py
