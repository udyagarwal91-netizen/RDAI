"""Remembers the result of each connection test so the Home page can show
a checklist ("4 of 5 connected") without re-testing every time."""

import json
from datetime import datetime

from app.db import connect


def save_test_result(brand: str, service: str, ok: bool, message: str) -> None:
    value = json.dumps({"ok": ok, "message": message, "at": datetime.now().strftime("%d %b %Y, %I:%M %p")})
    with connect() as conn:
        conn.execute("INSERT INTO app_state(brand, key, value) VALUES (?, ?, ?) "
                     "ON CONFLICT(brand, key) DO UPDATE SET value = excluded.value",
                     (brand, f"test:{service}", value))


def clear_test_result(brand: str, service: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE brand = ? AND key = ?", (brand, f"test:{service}"))


def test_results(brand: str) -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_state WHERE brand = ? AND key LIKE 'test:%'",
                            (brand,)).fetchall()
    return {r["key"][5:]: json.loads(r["value"]) for r in rows}
