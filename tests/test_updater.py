import pytest

from rdai import updater


def release(tag, assets=("RDAI-windows.zip",)):
    return {"tag_name": tag, "published_at": "2026-10-23T08:05:00Z",
            "assets": [{"name": a, "browser_download_url": f"https://example/{a}"} for a in assets]}


def test_newer_build_is_available(monkeypatch):
    monkeypatch.setattr(updater, "BUILD", "3")
    info = updater.check_for_update(fetch=lambda url, t: release("build-5"))
    assert (info["current"], info["latest"], info["available"]) == (3, 5, True)
    assert info["download_url"].endswith("RDAI-windows.zip")


def test_same_build_is_not_an_update(monkeypatch):
    monkeypatch.setattr(updater, "BUILD", "5")
    assert updater.check_for_update(fetch=lambda url, t: release("build-5"))["available"] is False


def test_bad_release_raises(monkeypatch):
    with pytest.raises(ValueError):
        updater.check_for_update(fetch=lambda url, t: release("v1.0.0"))
    with pytest.raises(ValueError):
        updater.check_for_update(fetch=lambda url, t: release("build-6", assets=()))


def test_source_checkout_cannot_self_update():
    assert updater.current_build() is None and not updater.can_self_update()


def test_script_keeps_data_and_restarts():
    s = updater.updater_script(1234, r"C:\tmp\new\RDAI", r"D:\Shop\RDAI")
    assert 'PID eq 1234' in s
    assert r'/MIR /XD "D:\Shop\RDAI' in s and 'data"' in s
    assert r'start "" "D:\Shop\RDAI' in s


def test_backup_database(tmp_path):
    db = tmp_path / "data" / "rdai.db"
    db.parent.mkdir()
    db.write_bytes(b"x")
    b = updater.backup_database(str(db))
    assert open(b, "rb").read() == b"x" and "backups" in b
