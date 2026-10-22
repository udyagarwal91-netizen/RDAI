"""Reading and saving API keys.

Keys live in the .env file (on your laptop) or in the host's environment
variables screen (on Railway/Render). This module is the only code that
reads or writes them. Rules it enforces:

- A key is never returned in full to the web page, only masked.
- Saving a key rewrites just that one line of .env and keeps your comments.
- The placeholder text "paste-your-key-here" counts as "no key yet".
"""

import os
import re
import secrets
import shutil
import threading
from pathlib import Path

from dotenv import load_dotenv

from app import ROOT

ENV_PATH = Path(os.environ.get("ENV_FILE", ROOT / ".env"))
ENV_EXAMPLE_PATH = ROOT / ".env.example"
PLACEHOLDER = "paste-your-key-here"

_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_write_lock = threading.Lock()


def ensure_env_file() -> None:
    """Create .env from .env.example the first time the app runs."""
    if not ENV_PATH.exists() and ENV_EXAMPLE_PATH.exists():
        shutil.copy(ENV_EXAMPLE_PATH, ENV_PATH)
        _make_private(ENV_PATH)


def load_env() -> None:
    """Load .env into the environment.

    Values already set by the host (Railway/Render) win over the file.
    """
    ensure_env_file()
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)
    if not get("FLASK_SECRET_KEY"):
        # Signs the Settings-page login cookie. Made once, kept forever.
        save("FLASK_SECRET_KEY", secrets.token_hex(32))


def get(name: str) -> str | None:
    """Return the key's value, or None if it is empty or still the placeholder."""
    value = (os.environ.get(name) or "").strip()
    if not value or value == PLACEHOLDER:
        return None
    return value


def save(name: str, value: str) -> None:
    """Save one key to .env and make it live immediately (no restart)."""
    if not _NAME_RE.match(name):
        raise ValueError(f"Not a valid key name: {name!r}")
    value = value.strip()
    if any(c in value for c in "\r\n\0"):
        raise ValueError("The key contains a line break. Copy it again without extra lines.")
    if any(c.isspace() for c in value) or '"' in value or "'" in value:
        raise ValueError("The key contains spaces or quotes. Copy just the key itself.")

    with _write_lock:
        ensure_env_file()
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
        new_line = f"{name}={value}"
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == name and not line.lstrip().startswith("#"):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
        tmp = ENV_PATH.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _make_private(tmp)
        tmp.replace(ENV_PATH)
        os.environ[name] = value


def mask(value: str | None) -> str:
    """Show only the start and end of a key, e.g. AIza••••••1234."""
    if not value:
        return "not set"
    if len(value) <= 10:
        return "•" * 6
    return f"{value[:4]}••••••{value[-4:]}"


def all_secret_values() -> list[str]:
    """Every key value currently loaded, for scrubbing logs and error messages."""
    from app.keys import KEYS  # local import avoids a circular import

    # endswith() also catches client-brand keys like ACME_META_ACCESS_TOKEN.
    suffixes = tuple(k.name for k in KEYS if k.secret) + (
        "FLASK_SECRET_KEY", "SETTINGS_PASSWORD_HASH", "SETTINGS_PASSWORD")
    values = [get(n) for n in os.environ if n.endswith(suffixes)]
    return [v for v in values if v and len(v) >= 8]


def scrub(text: str) -> str:
    """Replace any key that appears in the text with its masked form."""
    for value in all_secret_values():
        if value in text:
            text = text.replace(value, mask(value))
    return text


def _make_private(path: Path) -> None:
    try:
        path.chmod(0o600)  # only your user account can read it
    except OSError:
        pass  # Windows ignores this; the file is still git-ignored
