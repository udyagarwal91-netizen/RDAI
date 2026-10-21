#!/bin/bash
# Double-click this file to start the agent on a Mac.
# First run takes a few minutes (it installs what the agent needs).
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null; then
  echo "Python is not installed. See README.md, step 1."; read -r -p "Press Enter to close"; exit 1
fi
if [ ! -d .venv ]; then
  echo "Setting up for the first time..."
  python3 -m venv .venv || { read -r -p "Could not create .venv. Press Enter to close"; exit 1; }
fi
while true; do
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt \
    || { read -r -p "Install failed. Check your internet. Press Enter to close"; exit 1; }
  .venv/bin/python run.py
  # Exit code 3 means "I just updated myself, start me again".
  [ $? -eq 3 ] || break
  echo "Restarting after update..."
done
read -r -p "The agent has stopped. Press Enter to close"
