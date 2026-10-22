import shutil

import pytest

from app import BRANDS_DIR, brand_editor, brands, db, secrets_store


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A throwaway .env, database and copy of the brands folder for each test."""
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=paste-your-key-here\n", encoding="utf-8")
    monkeypatch.setattr(secrets_store, "ENV_PATH", env)
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FLASK_SECRET_KEY",
                 "SETTINGS_PASSWORD_HASH", "SETTINGS_PASSWORD", "META_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    brands_copy = tmp_path / "brands"
    shutil.copytree(BRANDS_DIR, brands_copy)
    monkeypatch.setattr(brands, "BRANDS_DIR", brands_copy)
    monkeypatch.setattr(brand_editor, "BRANDS_DIR", brands_copy)

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent.db")
    db.init_db()
    return tmp_path
