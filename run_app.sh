#!/usr/bin/env bash
# Start the RDAI stock app (Linux/Mac). First run installs everything.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
[ -f data/rdai.db ] || .venv/bin/python -m rdai.seed
exec .venv/bin/streamlit run app.py "$@"
