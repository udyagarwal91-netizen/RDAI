"""One-click updates from GitHub.

"Update agent" downloads the newest version of the code and copies it over
this folder, but NEVER touches your own things:
  - .env (your keys), data/ (database, generated images), logs/
  - brands/: files you already have are kept as they are; only brand-new
    files (e.g. a new post template) are added.
Then the agent restarts itself (the start file brings it back up).
"""

import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from app import DATA_DIR, ROOT, secrets_store

log = logging.getLogger(__name__)

REPO = os.environ.get("UPDATE_REPO", "udyagarwal91-netizen/rdai")
BRANCH = os.environ.get("UPDATE_BRANCH", "claude/new-session-dm5omt")
API = "https://api.github.com"
VERSION_FILE = DATA_DIR / "version.json"
RESTART_EXIT_CODE = 3  # the start file sees this and starts the agent again

NEVER_TOUCH = {".env", ".venv", "data", "logs", "media", ".git",
               # The start file is still running while the update happens; Windows and Mac read it line by line,
               # so changing it mid-run makes them run garbage. Start files stay as installed.
               "start-windows.bat", "start-mac.command"}
KEEP_EXISTING = {"brands"}  # only add new files here, never overwrite


class UpdateError(Exception):
    pass


def _headers() -> dict:
    token = secrets_store.get("GITHUB_TOKEN")
    if not token:
        raise UpdateError("Add your GitHub key first (Connect accounts → Updates).")
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def installed() -> dict:
    """{"sha": ..., "date": ...} of the installed version, or {} if unknown (installed from a ZIP)."""
    try:
        return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def latest() -> dict:
    """The newest version on GitHub: {"sha", "date", "message"}."""
    try:
        r = requests.get(f"{API}/repos/{REPO}/commits/{BRANCH}", headers=_headers(), timeout=20)
    except requests.RequestException:
        raise UpdateError("Couldn't reach GitHub. Check your internet connection.") from None
    if r.status_code in (401, 403):
        raise UpdateError("GitHub refused the key. Make a new one (Connect accounts → Updates) and paste it again.")
    if r.status_code == 404:
        raise UpdateError("GitHub can't see the agent's code with this key. When making the key, choose "
                          f"'Only select repositories' → {REPO.split('/')[1]}, and Contents: Read-only.")
    if r.status_code != 200:
        raise UpdateError(f"GitHub replied with error {r.status_code}.")
    c = r.json()
    return {"sha": c["sha"], "date": c["commit"]["committer"]["date"][:10],
            "message": c["commit"]["message"].splitlines()[0]}


def check() -> dict:
    """Is there something newer? Returns {"installed", "latest", "available"}."""
    have, new = installed(), latest()
    return {"installed": have, "latest": new, "available": have.get("sha") != new["sha"]}


def apply_update() -> str:
    """Download the newest version and copy it over this folder. Returns a message."""
    new = latest()
    try:
        r = requests.get(f"{API}/repos/{REPO}/zipball/{new['sha']}", headers=_headers(), timeout=120)
    except requests.RequestException:
        raise UpdateError("The download was interrupted. Check your internet and try again.") from None
    if r.status_code != 200:
        raise UpdateError(f"GitHub replied with error {r.status_code} while downloading.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp).resolve()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            for member in z.namelist():  # refuse anything that would land outside the temp folder
                if not (tmp_root / member).resolve().is_relative_to(tmp_root):
                    raise UpdateError("The download looked wrong, so nothing was changed.")
            z.extractall(tmp_root)
        tops = [p for p in tmp_root.iterdir() if p.is_dir()]
        if len(tops) != 1 or not (tops[0] / "run.py").exists():
            raise UpdateError("The download looked wrong, so nothing was changed.")
        added, updated = _copy_over(tops[0], ROOT)

    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(json.dumps({**new, "installed_at": datetime.now().isoformat(timespec="seconds")}),
                            encoding="utf-8")
    log.info("Updated to %s (%s): %d files updated, %d added", new["sha"][:7], new["message"], updated, added)
    return f"Updated to the version from {new['date']} ({new['message']}). Restarting…"


def _copy_over(src: Path, dest: Path) -> tuple[int, int]:
    added = updated = 0
    for path in src.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(src)
        top = rel.parts[0]
        if top in NEVER_TOUCH:
            continue
        target = dest / rel
        if target.exists():
            if top in KEEP_EXISTING:
                continue
            if target.read_bytes() == path.read_bytes():
                continue
            updated += 1
        else:
            added += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return added, updated
