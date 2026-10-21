"""Updates must replace code but never touch keys, data, logs or the owner's brand files."""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from app import secrets_store, updater


def _zip(files: dict[str, str], top="udyagarwal91-netizen-rdai-abc1234") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name if name.startswith("../") else f"{top}/{name}", text)
    return buf.getvalue()


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    (root / "app").mkdir(parents=True)
    (root / "brands/skipperpacks").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "run.py").write_text("old run")
    (root / "app/planner.py").write_text("old planner")
    (root / ".env").write_text("GEMINI_API_KEY=secret")
    (root / "data/agent.db").write_text("my database")
    (root / "brands/skipperpacks/config.yaml").write_text("my colours")
    monkeypatch.setattr(updater, "ROOT", root)
    monkeypatch.setattr(updater, "VERSION_FILE", root / "data/version.json")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_test_123456789")
    return root


def _fake_github(monkeypatch, zip_bytes, sha="abc1234def"):
    def get(url, headers=None, timeout=None):
        assert headers["Authorization"].startswith("Bearer github_pat_")
        if "/commits/" in url:
            body = {"sha": sha, "commit": {"committer": {"date": "2026-10-22T10:00:00Z"}, "message": "Phase 2\n\nmore"}}
            return SimpleNamespace(status_code=200, json=lambda: body)
        return SimpleNamespace(status_code=200, content=zip_bytes)
    monkeypatch.setattr(updater.requests, "get", get)


def test_update_replaces_code_and_keeps_owner_files(install, monkeypatch):
    _fake_github(monkeypatch, _zip({
        "run.py": "new run", "app/planner.py": "new planner", "app/producer.py": "brand new module",
        ".env": "GEMINI_API_KEY=", "data/agent.db": "empty db",
        "brands/skipperpacks/config.yaml": "default colours",
        "brands/skipperpacks/templates/product.html": "new template",
    }))
    message = updater.apply_update()
    assert "Phase 2" in message
    assert (install / "run.py").read_text() == "new run"
    assert (install / "app/producer.py").read_text() == "brand new module"
    assert (install / ".env").read_text() == "GEMINI_API_KEY=secret"          # keys kept
    assert (install / "data/agent.db").read_text() == "my database"            # data kept
    assert (install / "brands/skipperpacks/config.yaml").read_text() == "my colours"  # settings kept
    assert (install / "brands/skipperpacks/templates/product.html").exists()   # new brand files added
    assert updater.installed()["sha"] == "abc1234def"
    assert updater.check()["available"] is False


def test_unsafe_zip_changes_nothing(install, monkeypatch):
    _fake_github(monkeypatch, _zip({"run.py": "new run", "../../evil.py": "x"}))
    with pytest.raises(updater.UpdateError, match="nothing was changed"):
        updater.apply_update()
    assert (install / "run.py").read_text() == "old run"


def test_missing_key_explains_what_to_do(install, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    with pytest.raises(updater.UpdateError, match="Connect accounts"):
        updater.check()


def test_update_refuses_while_busy(monkeypatch):
    from app import jobs

    monkeypatch.setattr(jobs, "_running", {("skipperpacks", "plan")})
    with pytest.raises(updater.UpdateError, match="busy"):
        jobs.update("skipperpacks")
