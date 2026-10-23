"""One-click updates for the Windows app.

Every successful GitHub build is published as a release tagged build-<N> with RDAI-windows.zip.
check_for_update() compares N with this app's build number; apply_update() downloads the zip,
backs up the database, and hands over to a small batch script that waits for RDAI.exe to close,
copies the new files over the old ones (never touching the data folder) and starts RDAI again.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from ._version import BUILD

REPO = "udyagarwal91-netizen/RDAI"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "RDAI-windows.zip"


def current_build():
    """This app's build number, or None when running from source."""
    return int(BUILD) if str(BUILD).isdigit() else None


def can_self_update():
    return getattr(sys, "frozen", False) and sys.platform == "win32" and current_build() is not None


def _get_json(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "RDAI"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check_for_update(timeout=6, fetch=_get_json):
    """Returns {"current", "latest", "available", "download_url", "published_at"}.
    Raises OSError / ValueError when GitHub can't be reached or the answer is unexpected."""
    rel = fetch(LATEST_URL, timeout)
    tag = rel.get("tag_name", "")
    if not tag.startswith("build-") or not tag[6:].isdigit():
        raise ValueError(f"Unexpected release tag {tag!r}")
    latest = int(tag[6:])
    asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if asset is None:
        raise ValueError(f"Release {tag} has no {ASSET_NAME}")
    cur = current_build()
    return {"current": cur, "latest": latest, "available": cur is None or latest > cur,
            "download_url": asset["browser_download_url"], "published_at": rel.get("published_at", "")}


def app_dir():
    return os.path.dirname(sys.executable)


def backup_database(db_path):
    """Copy the database next to itself before updating. Returns the backup path."""
    folder = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"rdai-before-update-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(db_path, dest)
    return dest


def updater_script(pid, new_dir, target_dir):
    """Batch script: wait for RDAI to exit, mirror the new files in (data folder excluded), restart."""
    return f"""@echo off
title Updating RDAI
echo Updating RDAI - please wait, do not close this window...
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
robocopy "{new_dir}" "{target_dir}" /MIR /XD "{os.path.join(target_dir, 'data')}" /R:5 /W:2 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 (
  echo.
  echo Update failed while copying files. Your data was not changed.
  echo Please download RDAI again from GitHub.
  pause
  exit /b 1
)
start "" "{os.path.join(target_dir, 'RDAI.exe')}"
exit
"""


def download_and_extract(url, progress=None):
    """Download the release zip and extract it. Returns the folder holding the new RDAI.exe."""
    work = tempfile.mkdtemp(prefix="rdai_update_")
    zip_path = os.path.join(work, ASSET_NAME)
    req = urllib.request.Request(url, headers={"User-Agent": "RDAI"})
    with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(os.path.join(work, "new"))
    for root, _dirs, files in os.walk(os.path.join(work, "new")):
        if "RDAI.exe" in files:
            return root
    raise ValueError("The downloaded update does not contain RDAI.exe")


def apply_update(info, db_path, progress=None):
    """Download, back up the data, start the updater script. The caller must then exit the app."""
    if not can_self_update():
        raise RuntimeError("Updates can only be installed in the Windows RDAI.exe")
    new_dir = download_and_extract(info["download_url"], progress)
    backup = backup_database(db_path)
    script = os.path.join(tempfile.gettempdir(), "rdai_update.bat")
    with open(script, "w") as f:
        f.write(updater_script(os.getpid(), new_dir, app_dir()))
    subprocess.Popen(["cmd", "/c", script], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return backup
