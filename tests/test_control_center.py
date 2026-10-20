"""Control Center checks: login, Connect (save & test), Brand forms, logo, activity."""

import re

import pytest

from app import brand_editor, secrets_store, status
from app.brands import BrandConfigError, load_brand

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def client(sandbox, monkeypatch):
    import app.web as web

    monkeypatch.setattr(web, "LOGS_DIR", sandbox / "logs")
    secrets_store.save("FLASK_SECRET_KEY", "x" * 64)
    flask_app = web.create_app()
    flask_app.testing = True
    c = flask_app.test_client()
    page = c.get("/setup-password").get_data(as_text=True)
    c.post("/setup-password", data={"csrf": _csrf(page), "password": "goodpass1", "confirm": "goodpass1"})
    return c


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _token(client) -> str:
    return _csrf(client.get("/").get_data(as_text=True))


def test_first_visit_asks_for_password(sandbox):
    import app.web as web

    secrets_store.save("FLASK_SECRET_KEY", "x" * 64)
    c = web.create_app().test_client()
    assert "/setup-password" in c.get("/").headers["Location"]


def test_home_shows_checklist_and_next_step(client):
    html = client.get("/").get_data(as_text=True)
    assert "0 of 5 accounts connected" in html
    assert "Connect <b>Telegram</b>" in html
    assert "coming in phase 1" in html


def test_connect_save_and_test_stores_key_masked(client, monkeypatch):
    import app.web as web

    monkeypatch.setattr(web, "run_test", lambda name, brand: (True, "Bot @x works."))
    r = client.post("/connect/telegram", data={"csrf": _token(client), "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOP"})
    assert r.status_code == 302
    assert secrets_store.get("TELEGRAM_BOT_TOKEN") == "123456:ABCDEFGHIJKLMNOP"
    html = client.get("/connect").get_data(as_text=True)
    assert "1234••••••MNOP" in html and "ABCDEFGHIJ" not in html
    assert "1 of 5 accounts connected" in client.get("/").get_data(as_text=True)


def test_shopify_store_address_is_normalised_and_saved(client, monkeypatch):
    import app.web as web

    monkeypatch.setattr(web, "run_test", lambda name, brand: (False, "x"))
    client.post("/connect/shopify", data={"csrf": _token(client),
                                          "store_domain": "https://admin.shopify.com/store/skipper-xyz/products"})
    assert load_brand("skipperpacks").config["shopify"]["store_domain"] == "skipper-xyz.myshopify.com"


@pytest.mark.parametrize("raw,expected", [
    ("skipper-xyz.myshopify.com", "skipper-xyz.myshopify.com"),
    ("https://skipper-xyz.myshopify.com/admin", "skipper-xyz.myshopify.com"),
    ("admin.shopify.com/store/skipper-xyz", "skipper-xyz.myshopify.com"),
    ("skipper-xyz", "skipper-xyz.myshopify.com"),
])
def test_normalize_shop_domain(raw, expected):
    assert brand_editor.normalize_shop_domain(raw) == expected


def test_public_website_is_rejected_with_help():
    with pytest.raises(BrandConfigError, match="public website"):
        brand_editor.normalize_shop_domain("skipperpacks.com")


def test_schedule_form_saves_slots_and_keeps_comments(client, sandbox):
    form = {"csrf": _token(client), "day_mon": "on", "time_mon": "18:00", "day_fri": "on", "time_fri": "09:15",
            "plan_day": "sat", "plan_time": "08:00", "plan_horizon_days": "14"}
    client.post("/brand/schedule", data=form)
    sched = load_brand("skipperpacks").config["schedule"]
    assert sched["slots"] == [{"day": "mon", "time": "18:00"}, {"day": "fri", "time": "09:15"}]
    assert sched["posts_per_week"] == 2 and sched["plan_horizon_days"] == 14
    assert "# Nothing posts without your" in (sandbox / "brands/skipperpacks/config.yaml").read_text()


def test_content_mix_must_total_100(client):
    form = {"csrf": _token(client), "max_reels_per_week": "4",
            "theme_product": "50", "theme_offer": "10", "theme_education": "20", "theme_lifestyle": "30",
            "format_template_image": "25", "format_carousel": "25", "format_gemini_image": "25", "format_gemini_reel": "25"}
    client.post("/brand/mix", data=form)
    assert "add up to 110%" in client.get("/brand").get_data(as_text=True)
    assert load_brand("skipperpacks").config["content_mix"]["themes"]["lifestyle"] == 20  # unchanged


def test_ids_stay_text_and_auto_publish_toggle(client):
    t = _token(client)
    client.post("/brand/basics", data={"csrf": t, "name": "Skipper Packs", "website": "https://skipperpacks.com",
                                       "facebook_page_id": "1234567890", "instagram_account_id": "17841400000000000"})
    meta = load_brand("skipperpacks").config["meta"]
    assert meta["facebook_page_id"] == "1234567890"  # text, not a number
    client.post("/brand/approval", data={"csrf": t, "auto_publish": "on"})
    assert load_brand("skipperpacks").auto_publish is True
    client.post("/brand/approval", data={"csrf": t})
    assert load_brand("skipperpacks").auto_publish is False


def test_hashtags_are_cleaned(client):
    form = {"csrf": _token(client), "language": "English with light Hinglish", "hinglish_share_percent": "25",
            "tone": "Calm.", "caption_length": "short", "emoji_level": "low", "call_to_action": "Shop now",
            "hashtags_always": "SkipperPacks, #Daily Essentials #Comfort", "hashtags_max": "10"}
    client.post("/brand/voice", data=form)
    assert load_brand("skipperpacks").config["hashtags"]["always"] == ["#SkipperPacks", "#DailyEssentials", "#Comfort"]


def test_logo_upload_and_preview(client):
    from io import BytesIO

    r = client.post("/brand/logo", data={"csrf": _token(client), "which": "main", "logo": (BytesIO(PNG), "logo.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    assert client.get("/brand/logo/main").data == PNG
    bad = client.post("/brand/logo", data={"csrf": _token(client), "which": "main",
                                           "logo": (BytesIO(b"<svg>"), "x.svg")}, content_type="multipart/form-data")
    assert bad.status_code == 302
    assert client.get("/brand/logo/main").data == PNG  # bad upload didn't replace the good one


def test_broken_change_is_rolled_back(sandbox):
    with pytest.raises(BrandConfigError):
        brand_editor.update_config("skipperpacks", {"content_mix.themes": {"product": 1}})
    assert load_brand("skipperpacks").config["content_mix"]["themes"]["product"] == 50


def test_status_round_trip(sandbox):
    status.save_test_result("skipperpacks", "gemini", True, "ok")
    assert status.test_results("skipperpacks")["gemini"]["ok"] is True


def test_activity_page_shows_logs(client, sandbox):
    (sandbox / "logs").mkdir()
    (sandbox / "logs" / "2026-10-20.log").write_text("2026-10-20 10:00:00  INFO     run   hello\n")
    assert "hello" in client.get("/activity").get_data(as_text=True)
    assert client.get("/activity?day=../../.env").status_code == 200  # ignored, not read


def test_csrf_required(client):
    assert client.post("/brand/approval", data={"auto_publish": "on"}).status_code == 400
