"""Agent HQ checks (Phase A): goals, dashboard, phone access, voice in the app, installable app files."""

import io
import subprocess
from datetime import date

import imageio_ffmpeg

from app import goals, secrets_store
from app.brands import load_brand
from test_control_center import _token, client  # noqa: F401 — shared fixture


def test_goals_start_from_brand_file(sandbox):
    brand = load_brand("skipperpacks")
    doc = goals.load(brand)
    roas = next(g for g in doc["goals"] if g["key"] == "blended_roas")
    assert roas["baseline"] == 5.9 and roas["targets"][90] == 10
    assert doc["guardrails"]["daily_ad_budget"] == 500
    assert doc["business"]["product_cost_pct"] is None


def test_expected_value_is_a_straight_line_between_milestones():
    g = {"baseline": 6, "targets": {30: 7, 60: 8, 90: 10}}
    assert goals.expected(g, 0) == 6 and goals.expected(g, 15) == 6.5 and goals.expected(g, 75) == 9
    assert goals.expected(g, 400) == 10
    assert goals.expected({"baseline": None, "targets": {30: 70, 60: None, 90: 85}}, 10) == 70


def test_progress_lights_and_agent_summary(sandbox):
    brand = load_brand("skipperpacks")
    start = date.fromisoformat(goals.load(brand)["start_date"])
    goals.record(brand, {"blended_roas": 6.6, "cpa": 80, "aov": None}, "test")
    today = date.fromordinal(start.toordinal() + 30)
    lights = {p["key"]: p["status"] for p in goals.progress(brand, today)}
    assert lights["blended_roas"] == "green"   # 6.6 ≥ 6.5 expected on day 30
    assert lights["cpa"] == "red"               # 80 vs 60 (lower is better)
    assert lights["aov"] == "nodata"            # never measured → no guess
    text = goals.summary_for_agents(brand)
    assert "GUARDRAILS" in text and "Daily ad budget" in text and "not given by the owner yet" in text


def test_goals_page_saves_and_rejects_bad_numbers(client):
    page = client.get("/goals").get_data(as_text=True)
    assert "Blended ROAS" in page and "Safety limits" in page
    form = {"csrf": _token(client), "start_date": "2026-11-05", "north_star": "ROAS 10",
            "blended_roas_baseline": "5.9", "blended_roas_30": "7", "blended_roas_60": "8", "blended_roas_90": "10",
            "guard_daily_ad_budget": "600", "guard_monthly_ad_budget": "18000", "guard_max_budget_increase_pct": "20",
            "guard_scale_only_if_roas_above": "8", "guard_ai_cost_monthly": "3000", "biz_product_cost_pct": "40%"}
    client.post("/goals", data=form)
    doc = goals.load(load_brand("skipperpacks"))
    assert doc["guardrails"]["daily_ad_budget"] == 600 and doc["business"]["product_cost_pct"] == 40
    assert next(g for g in doc["goals"] if g["key"] == "blended_roas")["targets"][30] == 7
    r = client.post("/goals", data={**form, "csrf": _token(client), "guard_daily_ad_budget": ""}, follow_redirects=True)
    assert "be empty" in r.get_data(as_text=True)
    assert goals.load(load_brand("skipperpacks"))["guardrails"]["daily_ad_budget"] == 600


def test_dashboard_shows_goals_and_phone_switch(client, monkeypatch):
    page = client.get("/").get_data(as_text=True)
    assert "🎯 Goals" in page and "Switch on phone access" in page and "Agent HQ" in page
    timers = []
    import app.web.hq as hq
    monkeypatch.setattr(hq.threading, "Timer", lambda s, fn: timers.append(s) or type("T", (), {"start": lambda self: None})())
    client.post("/phone", data={"csrf": _token(client), "on": "1"})
    assert secrets_store.get("LAN_ACCESS") == "1" and timers == [2.0]  # restart scheduled, not run in the test


def test_installable_app_files(client):
    m = client.get("/manifest.webmanifest").get_json()
    assert m["display"] == "standalone" and m["icons"][0]["src"] == "/app-icon-192.png"
    assert client.get("/app-icon-192.png").data[:4] == b"\x89PNG"
    assert b"serviceWorker" in client.get("/").data and b"skipWaiting" in client.get("/sw.js").data


def test_voice_in_the_app(client, monkeypatch):
    from app import assistant, voice
    monkeypatch.setattr(voice, "to_mp3", lambda data, suffix: b"mp3" + suffix.encode())
    seen = []
    monkeypatch.setattr(voice, "transcribe", lambda b, data, mime: seen.append((data, mime)) or "aaj ka plan dikhao")
    monkeypatch.setattr(assistant, "reply", lambda b, text: {"text": f"echo {text}", "confirmations": []})
    r = client.post("/chat/voice", data={"audio": (io.BytesIO(b"x"), "voice.mp4")},
                    headers={"X-CSRF-Token": _token(client)}, content_type="multipart/form-data")
    assert r.get_json() == {"heard": "aaj ka plan dikhao", "reply": "echo aaj ka plan dikhao"}
    assert seen == [(b"mp3.mp4", "audio/mp3")]


def test_browser_recording_converts_to_mp3(tmp_path):
    src = tmp_path / "beep.webm"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:a", "libopus", str(src)], check=True)
    from app import voice
    mp3 = voice.to_mp3(src.read_bytes(), ".webm")
    assert len(mp3) > 1000 and (mp3[:3] == b"ID3" or mp3[0] == 0xFF)


def test_brands_are_copied_to_the_cloud_disk_without_overwriting(sandbox, tmp_path, monkeypatch):
    from app import brands
    disk = tmp_path / "disk_brands"
    (disk / "skipperpacks").mkdir(parents=True)
    (disk / "skipperpacks" / "brand-rules.md").write_text("MY EDITED RULES", encoding="utf-8")
    monkeypatch.setattr(brands, "BRANDS_DIR", disk)
    copied = brands.seed_brands()
    assert copied > 3 and (disk / "skipperpacks" / "config.yaml").exists()
    assert (disk / "skipperpacks" / "brand-rules.md").read_text(encoding="utf-8") == "MY EDITED RULES"
    assert brands.seed_brands() == 0


def test_two_copies_warning(sandbox, monkeypatch):
    from app import telegram
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda b, t: sent.append(t))
    bot = telegram.Bot(lambda: None)
    bot._two_copies(load_brand("skipperpacks"))
    bot._two_copies(load_brand("skipperpacks"))  # not repeated within the hour
    assert len(sent) == 1 and "Two copies" in sent[0]


def test_online_updates_and_secure_cookies(sandbox, monkeypatch):
    import pytest as _pytest

    from app import jobs, updater
    import app.web as web
    monkeypatch.setenv("PORT", "8080")
    with _pytest.raises(updater.UpdateError, match="updates itself"):
        jobs.update("skipperpacks")
    secrets_store.save("FLASK_SECRET_KEY", "x" * 64)
    assert web.create_app().config["SESSION_COOKIE_SECURE"] is True
