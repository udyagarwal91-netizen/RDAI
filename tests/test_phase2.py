"""Phase 2 checks: captions, images, quality check + auto-fix, Telegram approval, Gemini answer parsing."""

import base64
import io
import json
import os
from datetime import date
from types import SimpleNamespace

import pytest
from PIL import Image

from app import checker, gemini, media, planner, producer, renderer, shopify, telegram, writer
from app.brands import load_brand
from app.db import connect
from test_phase1 import _plan, brand  # noqa: F401 — `brand` is a shared test fixture


def _jpeg(color=(200, 50, 50), size=(400, 500)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


class StudioClaude:
    """Stands in for Claude in all three roles: planner, caption writer, image checker."""

    def __init__(self, caption="Roz ka comfort, sorted. Pack of 3 from ₹249. Shop the pack at skipperpacks.com",
                 checks=(True,), style="product_only"):
        self.calls, self.caption, self.checks, self.style = [], caption, list(checks), style
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        props = kw["output_config"]["format"]["schema"]["properties"]
        if "posts" in props:  # planner
            items = props["posts"]["items"]["properties"]
            answer = _plan(items["slot"]["enum"], items["styles"]["items"]["enum"], 2, "template_image")
        elif "caption" in props:  # writer
            answer = {"caption": self.caption, "hashtags": ["#Innerwear", "ValuePack"], "headline": "Roz ka comfort",
                      "subline": "3-pc Active Brief", "photo_index": 0, "image_style": self.style,
                      "scene": "Folded packs on a wooden shelf, morning light",
                      "slides": [{"headline": "Wash cold", "body": "Keeps the waistband springy."},
                                 {"headline": "Air dry", "body": "Shade, not sun."}]}
        else:  # checker
            ok = self.checks.pop(0) if self.checks else True
            answer = {"passed": ok, "problems": [] if ok else ["the waistband colour is wrong"]}
        usage = SimpleNamespace(input_tokens=3000, output_tokens=800, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(answer))])


@pytest.fixture
def studio(brand, monkeypatch):
    """A brand with synced products, a plan, and no real network: Claude, Gemini, photos, browser are stand-ins."""
    shopify.sync_products(brand)
    fake = StudioClaude()
    monkeypatch.setattr(producer.writer.claude, "client_for", lambda b: fake)
    monkeypatch.setattr(media, "download", lambda url, width=1024: _jpeg())
    renders = []
    monkeypatch.setattr(renderer, "render", lambda b, t, pages, size=(1080, 1350): renders.append((t, pages)) or [_jpeg() for _ in pages])
    gem_calls = []

    def fake_gemini(b, scene, style, refs, aspect="4:5", extra_instructions=""):
        gem_calls.append(extra_instructions)
        return _jpeg((10, 10, 10), (900, 1100)), 0.05
    monkeypatch.setattr(gemini, "generate_image", fake_gemini)
    sent = []
    monkeypatch.setattr(telegram, "send_draft", lambda b, d: sent.append(d["id"]))
    planner.make_plan(brand, start=date(2099, 1, 5), client=fake)
    return SimpleNamespace(brand=load_brand("skipperpacks"), claude=fake, renders=renders, gemini=gem_calls, sent=sent)


def _plan_row(fmt="template_image"):
    with connect() as conn:
        row = dict(conn.execute("SELECT * FROM content_plan ORDER BY scheduled_for LIMIT 1").fetchone())
    row["format"] = fmt
    return row


def test_template_draft_uses_shopify_price_on_image(studio):
    draft_id = producer.make_draft(studio.brand, _plan_row("template_image"), client=studio.claude)
    d = producer.get_draft(studio.brand, draft_id)
    template, pages = studio.renders[-1]
    assert template == "post.html" and pages[0]["price"] == "from ₹249" and pages[0]["mrp"] == "₹339"
    assert pages[0]["badge"] == "3-PC PACK"
    assert d["status"] == "pending" and d["warnings"] == [] and len(d["files"]) == 1 and d["files"][0].exists()
    assert d["caption"].endswith("#SkipperPacks #DailyEssentials #Innerwear #ValuePack")
    with connect() as conn:
        assert conn.execute("SELECT status FROM content_plan WHERE id = ?", (d["plan_id"],)).fetchone()[0] == "drafted"


def test_carousel_has_cover_middle_and_price_slides(studio):
    producer.make_draft(studio.brand, _plan_row("carousel"), client=studio.claude)
    template, pages = studio.renders[-1]
    assert template == "slide.html" and [p["kind"] for p in pages] == ["cover", "info", "info", "end"]
    assert pages[-1]["price"] == "from ₹249" and pages[0]["total"] == 4


def test_ai_image_flagged_is_remade_once_then_marked(studio):
    studio.claude.checks = [False, False]
    draft_id = producer.make_draft(studio.brand, _plan_row("gemini_image"), client=studio.claude)
    d = producer.get_draft(studio.brand, draft_id)
    assert studio.gemini == ["", "the waistband colour is wrong"]  # second try told what to fix
    assert d["warnings"] == ["check carefully: the waistband colour is wrong"]
    assert d["attempt"] == 2 and d["image_style"] == "product_only"


def test_ai_image_fixed_on_second_try_has_no_warning(studio):
    studio.claude.checks = [False, True]
    d = producer.get_draft(studio.brand, producer.make_draft(studio.brand, _plan_row("gemini_image"), client=studio.claude))
    assert d["warnings"] == []


def test_invented_price_gets_one_rewrite_then_warning(studio):
    studio.claude.caption = "Only ₹99 today! 50% off"
    d = producer.get_draft(studio.brand, producer.make_draft(studio.brand, _plan_row(), client=studio.claude))
    writer_calls = [c for c in studio.claude.calls if "caption" in c["output_config"]["format"]["schema"]["properties"]]
    assert len(writer_calls) == 2 and "fix them" in json.dumps(writer_calls[1]["messages"])
    assert any("₹99" in w for w in d["warnings"]) and any("%" in w for w in d["warnings"])


def test_writer_sends_product_photos_and_rules(studio):
    producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    call = next(c for c in studio.claude.calls if "caption" in c["output_config"]["format"]["schema"]["properties"])
    content = call["messages"][0]["content"]
    assert any(b["type"] == "image" and "width=600" in b["source"]["url"] for b in content)
    assert "prices by size: 80cm ₹249" in content[0]["text"]
    assert "never be suggestive" in call["system"][0]["text"]


def test_approve_reject_and_regenerate(studio, monkeypatch):
    from app import jobs

    first = producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    producer.decide(studio.brand, first, "approve")
    assert producer.get_draft(studio.brand, first)["status"] == "approved"
    second = producer.make_draft(studio.brand, _plan_row("carousel"), client=studio.claude)
    producer.remake(studio.brand, second, client=studio.claude)
    d = producer.get_draft(studio.brand, second)
    assert d["attempt"] == 2 and d["status"] == "pending"
    producer.decide(studio.brand, second, "reject")
    with connect() as conn:
        decisions = [r[0] for r in conn.execute("SELECT decision FROM approvals ORDER BY id")]
    assert decisions == ["approve", "reject"]


def test_auto_publish_approves_clean_drafts(studio):
    from app import brand_editor

    brand_editor.update_config("skipperpacks", {"auto_publish": True})
    brand = load_brand("skipperpacks")
    d = producer.get_draft(brand, producer.make_draft(brand, _plan_row(), client=studio.claude))
    assert d["status"] == "approved"


def test_drafts_job_makes_and_sends(studio, monkeypatch):
    from app import jobs

    monkeypatch.setattr(producer, "due_plan_rows", lambda b, days=7, limit=10: [_plan_row()])
    msg = jobs.drafts("skipperpacks")
    assert "Made 1 draft" in msg and len(studio.sent) == 1


def test_find_style_for_image_command(studio):
    assert producer.find_style(studio.brand, "active brief 3")["title"] == "Colin Active Brief 3 Pcs Pack"
    assert producer.find_style(studio.brand, "classic trunk")["title"] == "Colin Classic Trunk 2 Pcs Pack"


def test_telegram_buttons_and_caption_edit(studio, monkeypatch):
    from app.db import get_state

    draft_id = producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    calls, messages = [], []
    monkeypatch.setattr(telegram, "_call", lambda token, method, **kw: calls.append((method, kw)) or {"message_id": 7})
    monkeypatch.setattr(telegram, "send_message", lambda b, text: messages.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    bot = telegram.Bot(lambda: studio.brand)

    bot.handle_button(studio.brand, {"id": "q1", "from": {"id": 555}, "data": f"a:{draft_id}"}, "12345")
    assert producer.get_draft(studio.brand, draft_id)["status"] == "pending"  # stranger ignored

    bot.handle_button(studio.brand, {"id": "q2", "from": {"id": 12345}, "data": f"e:{draft_id}"}, "12345")
    assert get_state("skipperpacks", "awaiting_caption") == str(draft_id)
    bot.handle(studio.brand, {"chat": {"id": 12345}, "text": "New words, only ₹99!"}, "12345")
    d = producer.get_draft(studio.brand, draft_id)
    assert d["caption"].startswith("New words") and "#SkipperPacks" in d["caption"]
    assert any("₹99" in m for m in messages)  # warned about the invented price

    bot.handle_button(studio.brand, {"id": "q3", "from": {"id": 12345}, "data": f"a:{draft_id}",
                                     "message": {"message_id": 7}}, "12345")
    assert producer.get_draft(studio.brand, draft_id)["status"] == "approved"
    bot.handle_button(studio.brand, {"id": "q4", "from": {"id": 12345}, "data": f"x:{draft_id}"}, "12345")
    assert producer.get_draft(studio.brand, draft_id)["status"] == "approved"  # can't reject after approving
    assert ("answerCallbackQuery", {"callback_query_id": "q4", "text": "Already approved."}) in calls


def test_draft_message_text(studio):
    d = producer.get_draft(studio.brand, producer.make_draft(studio.brand, _plan_row(), client=studio.claude))
    text = telegram.format_draft(studio.brand, d)
    assert f"Draft #{d['id']}" in text and "from ₹249" in text and "Cost about US$" in text
    assert telegram.draft_buttons(d["id"])["inline_keyboard"][0][0]["callback_data"] == f"a:{d['id']}"


def test_gemini_reads_image_from_model_output_only(brand, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST1234567890")
    sent = {}
    made = base64.b64encode(b"MADE").decode()
    echo = base64.b64encode(b"INPUT").decode()

    def post(url, json=None, headers=None, timeout=None):
        sent.update(json)
        body = {"status": "completed", "steps": [
            {"type": "user_input", "content": [{"type": "image", "data": echo}]},
            {"type": "model_output", "content": [{"type": "image", "mime_type": "image/png", "data": made}]}]}
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: body)
    monkeypatch.setattr(gemini.requests, "post", post)
    image, cost = gemini.generate_image(brand, "flat-lay", "product_only", [b"ref"])
    assert image == b"MADE" and cost == 0.0504
    assert sent["model"] == "gemini-nano-banana-2.1" and sent["response_format"]["aspect_ratio"] == "4:5"
    assert "No people at all" in sent["input"][0]["text"] and sent["input"][1]["type"] == "image"


def test_gemini_safety_block_is_explained(brand, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST1234567890")
    body = {"status": "failed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": "no"}]}]}
    monkeypatch.setattr(gemini.requests, "post", lambda *a, **k: SimpleNamespace(
        status_code=200, headers={"content-type": "application/json"}, json=lambda: body))
    with pytest.raises(gemini.GeminiError, match="safety filter"):
        gemini.generate_image(brand, "x", "website_model", [b"ref"])


def test_to_jpeg_resizes_and_stamps_logo(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (300, 100), (255, 255, 255, 255)).save(logo)
    out = Image.open(io.BytesIO(media.to_jpeg(_jpeg(size=(1000, 1000)), logo=logo)))
    assert out.size == (1080, 1350) and out.format == "JPEG"
    assert out.getpixel((1080 - 40, 1350 - 40))[0] > 200  # white logo in the corner


@pytest.mark.skipif(not os.environ.get("CHROMIUM_PATH"), reason="needs the real browser")
def test_real_render(brand):
    renderer.start_browser_install()
    pages = renderer.render(brand, "post.html", [{"photo": renderer.data_uri(_jpeg()), "headline": "Hello",
                                                  "subline": "", "badge": "3-PC PACK", "price": "from ₹249",
                                                  "mrp": "₹339", "cta": "Shop"}])
    assert Image.open(io.BytesIO(pages[0])).size == (1080, 1350)
