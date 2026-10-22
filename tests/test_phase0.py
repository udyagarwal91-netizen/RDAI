"""Phase 0 checks: key storage, masking, log scrubbing, brand config, Settings page."""

import logging
import os

import pytest

from app import secrets_store


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# comment stays\nGEMINI_API_KEY=paste-your-key-here\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
    monkeypatch.setattr(secrets_store, "ENV_PATH", path)
    for name in ("GEMINI_API_KEY", "TELEGRAM_CHAT_ID", "ANTHROPIC_API_KEY", "FLASK_SECRET_KEY",
                 "SETTINGS_PASSWORD_HASH", "SETTINGS_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    return path


def test_placeholder_counts_as_missing(env_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "paste-your-key-here")
    assert secrets_store.get("GEMINI_API_KEY") is None


def test_save_replaces_line_and_keeps_comments(env_file):
    secrets_store.save("GEMINI_API_KEY", "AIzaSyTESTKEY1234567890")
    text = env_file.read_text(encoding="utf-8")
    assert "# comment stays" in text
    assert "GEMINI_API_KEY=AIzaSyTESTKEY1234567890" in text
    assert text.count("GEMINI_API_KEY=") == 1
    assert os.environ["GEMINI_API_KEY"] == "AIzaSyTESTKEY1234567890"


def test_save_appends_new_key(env_file):
    secrets_store.save("ANTHROPIC_API_KEY", "sk-ant-abcdefghijkl")
    assert "ANTHROPIC_API_KEY=sk-ant-abcdefghijkl" in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["two words", "line\nbreak", '"quoted"'])
def test_save_rejects_bad_values(env_file, bad):
    with pytest.raises(ValueError):
        secrets_store.save("GEMINI_API_KEY", bad)


def test_mask():
    assert secrets_store.mask("AIzaSyTESTKEY1234") == "AIza••••••1234"
    assert secrets_store.mask("short") == "••••••"
    assert secrets_store.mask(None) == "not set"


def test_logs_never_contain_keys(env_file, caplog):
    from app.logging_setup import ScrubSecrets

    secrets_store.save("GEMINI_API_KEY", "AIzaSyTOPSECRET99999")
    logger = logging.getLogger("scrub-test")
    logger.addFilter(ScrubSecrets())
    with caplog.at_level(logging.INFO):
        logger.info("calling https://example.com/?key=%s", "AIzaSyTOPSECRET99999")
    assert "TOPSECRET" not in caplog.text
    assert "AIza••••••9999" in caplog.text


def test_skipperpacks_config_loads():
    from app.brands import load_brand

    brand = load_brand("skipperpacks")
    assert brand.name == "Skipper Packs"
    assert brand.auto_publish is False
    assert "50+ years" in brand.rules
    assert brand.config["content_mix"]["max_reels_per_week"] == 4


def test_db_creates_all_tables(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "agent.db")
    db.init_db()
    with db.connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"products", "content_plan", "drafts", "approvals", "posts", "insights"} <= tables
