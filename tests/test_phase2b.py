"""Phase 2b checks: AI reels — clip from Gemini Omni, our own text/price/logo layers, weekly cap, fallback,
Telegram video, and publishing reels to Instagram and Facebook. FFmpeg runs for real (it's bundled)."""

import base64
import io
import json
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from app import brand_editor, producer, publisher, renderer, telegram, video
from app.brands import load_brand
from app.db import connect
from test_phase1 import brand  # noqa: F401 — shared fixture
from test_phase2 import _plan_row, studio  # noqa: F401
from test_phase3 import FakeMeta, live  # noqa: F401

REAL_SEND_DRAFT = telegram.send_draft  # the shared fixture swaps it for a recorder


def make_clip(seconds=4, size="360x640", audio=True) -> bytes:
    """A small real MP4 standing in for Gemini's video."""
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "c.mp4"
        cmd = [video.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"testsrc2=size={size}:rate=24:duration={seconds}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
        subprocess.run(cmd, check=True)
        return out.read_bytes()


def _png_layers(brand, template, pages, size=(1080, 1350), transparent=False):
    assert template == "reel_overlay.html" and transparent and size == (1080, 1920)
    layers = []
    for _ in pages:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        img.paste((255, 255, 255, 255), (100, 100, 500, 200))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        layers.append(buf.getvalue())
    return layers


@pytest.fixture(autouse=True)
def fast_encoding(monkeypatch):
    monkeypatch.setattr(video, "ENCODE_PRESET", "ultrafast")


@pytest.fixture
def reels(studio, monkeypatch):
    clip = make_clip()
    calls = []

    def fake_clip(b, scene, style, reference, extra_instructions=""):
        calls.append({"scene": scene, "style": style, "extra": extra_instructions})
        return clip, 0.8
    monkeypatch.setattr(video, "generate_clip", fake_clip)
    real_render = renderer.render
    monkeypatch.setattr(renderer, "render", lambda b, t, pages, size=(1080, 1350), transparent=False:
                        _png_layers(b, t, pages, size, transparent) if t == "reel_overlay.html"
                        else real_render(b, t, pages, size, transparent))
    studio.clip_calls = calls
    return studio


def test_compose_makes_instagram_ready_reel(brand, monkeypatch):
    monkeypatch.setattr(renderer, "render", _png_layers)
    for audio in (True, False):
        reel = video.compose(brand, make_clip(audio=audio), {"headline": "Hi", "product": "P", "price": "₹249"})
        info = subprocess.run([video.ffmpeg(), "-hide_banner", "-i", "-"], input=reel, capture_output=True).stderr.decode()
        assert "1080x1920" in info and "h264" in info and "aac" in info  # silent track added when the clip has none
        assert abs(video.duration(reel) - 4.0) < 0.2
    assert len(video.frames(reel)) == 3


def test_reel_draft_saves_video_and_cover_with_shopify_price(reels, monkeypatch):
    captured = {}
    real_compose = video.compose
    monkeypatch.setattr(video, "compose", lambda b, clip, fields: captured.update(fields) or real_compose(b, clip, fields))
    d = producer.get_draft(reels.brand, producer.make_draft(reels.brand, _plan_row("gemini_reel"), client=reels.claude))
    assert d["format"] == "gemini_reel" and d["is_reel"] and [f.name for f in d["files"]] == ["reel.mp4", "cover.jpg"]
    assert captured["price"] == "from ₹249" and captured["badge"] == "3-PC PACK" and captured["headline"] == "Roz ka comfort"
    assert d["cost_usd"] >= 0.8  # the clip's cost is counted
    assert reels.clip_calls[0]["scene"].startswith("Folded packs")


def test_flagged_reel_is_remade_once(reels):
    reels.claude.checks = [False, True]
    producer.make_draft(reels.brand, _plan_row("gemini_reel"), client=reels.claude)
    assert len(reels.clip_calls) == 2 and "waistband colour is wrong" in reels.clip_calls[1]["extra"]


def test_weekly_reel_cap_falls_back_to_image(reels):
    brand_editor.update_config("skipperpacks", {"content_mix.max_reels_per_week": 1})
    brand = load_brand("skipperpacks")
    first = producer.get_draft(brand, producer.make_draft(brand, _plan_row("gemini_reel"), client=reels.claude))
    second = producer.get_draft(brand, producer.make_draft(brand, _plan_row("gemini_reel"), client=reels.claude))
    assert first["format"] == "gemini_reel"
    assert second["format"] == "gemini_image" and any("weekly reel limit" in w for w in second["warnings"])
    # Regenerating the reel itself doesn't count against the cap.
    producer.remake(brand, first["id"], client=reels.claude)
    assert producer.get_draft(brand, first["id"])["format"] == "gemini_reel"


def test_failed_reel_becomes_image_with_reason(reels, monkeypatch):
    def broken(*a, **k):
        raise video.VideoError("Gemini's video limit was reached for now.")
    monkeypatch.setattr(video, "generate_clip", broken)
    d = producer.get_draft(reels.brand, producer.make_draft(reels.brand, _plan_row("gemini_reel"), client=reels.claude))
    assert d["format"] == "gemini_image" and any("video limit" in w for w in d["warnings"])


def test_gemini_video_request_and_answer(brand, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST1234567890")
    clip = make_clip(seconds=2)
    sent = {}

    def post(url, json=None, headers=None, timeout=None):
        sent.update(json)
        body = {"status": "completed", "steps": [
            {"type": "user_input", "content": [{"type": "image", "data": "eA=="}]},
            {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4",
                                                  "data": base64.b64encode(clip).decode()}]}]}
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: body)
    monkeypatch.setattr(video.requests, "post", post)
    data, cost = video.generate_clip(brand, "unpacking a gym bag", "product_only", b"ref")
    assert data == clip and abs(cost - 0.2) < 0.02  # 2 seconds × US$0.10
    assert sent["model"] == "gemini-omni-1.1-flash"
    assert sent["response_format"] == {"type": "video", "aspect_ratio": "9:16", "resolution": "720p"}
    assert sent["generation_config"]["video_config"]["task"] == "reference_to_video"
    assert "No on-screen text" in sent["input"][0]["text"] and "No people at all" in sent["input"][0]["text"]


def test_gemini_video_file_link_is_downloaded(brand, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST1234567890")
    clip = make_clip(seconds=1)
    body = {"status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "video", "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc123"}]}]}
    monkeypatch.setattr(video.requests, "post", lambda *a, **k: SimpleNamespace(
        status_code=200, headers={"content-type": "application/json"}, json=lambda: body))
    gets = []

    def get(url, params=None, headers=None, timeout=None):
        gets.append(url)
        if url.endswith(":download"):
            return SimpleNamespace(status_code=200, content=clip)
        return SimpleNamespace(json=lambda: {"state": "ACTIVE"})
    monkeypatch.setattr(video.requests, "get", get)
    data, _ = video.generate_clip(brand, "x", "product_only", b"ref")
    assert data == clip and gets[-1].endswith("files/abc123:download")


class ReelMeta(FakeMeta):
    def request(self, method, url, data=None, params=None, files=None, timeout=None):
        if url.endswith("/videos"):
            self.calls.append((method, "videos", dict(data or {}), bool(files)))
            return self._resp({"id": "vid1"})
        args = dict(params or {})
        if args.get("fields") == "permalink_url,post_id":
            return self._resp({"permalink_url": "/skipperpacks/videos/vid1", "post_id": "111_999"})
        return super().request(method, url, data, params, files, timeout)


def test_reel_publishes_to_instagram_and_facebook(live, reels, monkeypatch):
    meta = ReelMeta()
    monkeypatch.setattr(publisher.requests, "request", meta.request)
    uploads = []

    def rupload(url, data=None, headers=None, timeout=None):
        uploads.append((url, headers, len(data)))
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                               json=lambda: {"success": True, "message": "Upload successful."})
    monkeypatch.setattr(publisher.requests, "post", rupload)
    draft_id = producer.make_draft(live.brand, _plan_row("gemini_reel"), client=live.claude)
    producer.decide(live.brand, draft_id, "approve")
    with connect() as conn:
        conn.execute("UPDATE posts SET scheduled_for = '2000-01-01T00:00:00+05:30' WHERE draft_id = ?", (draft_id,))
        conn.execute("UPDATE posts SET attempts = 0")
    from datetime import timedelta
    with connect() as conn:  # due a minute ago (not "very late")
        conn.execute("UPDATE posts SET scheduled_for = ? WHERE draft_id = ?",
                     ((publisher._now(live.brand) - timedelta(minutes=1)).isoformat(), draft_id))
    assert publisher.publish_due(live.brand, live.notify) == 2
    container = next(a for m, p, a, f in meta.calls if p == "222/media")
    assert container["media_type"] == "REELS" and container["upload_type"] == "resumable"
    url, headers, size = uploads[0]
    assert "rupload.facebook.com/ig-api-upload/v26.0/container" in url
    assert headers["Authorization"] == "OAuth PAGE_TOKEN" and headers["offset"] == "0" and int(headers["file_size"]) == size
    fb = next(a for m, p, a, f in meta.calls if p == "videos")
    assert "#SkipperPacks" in fb["description"]
    with connect() as conn:
        fb_post = conn.execute("SELECT * FROM posts WHERE draft_id = ? AND platform = 'facebook'", (draft_id,)).fetchone()
    assert fb_post["platform_post_id"] == "111_999" and fb_post["permalink"] == "https://www.facebook.com/skipperpacks/videos/vid1"
    assert not any(p == "111/photos" for m, p, a, f in meta.calls)  # reels never go through the photo route


def test_telegram_sends_reel_as_video(reels, monkeypatch):
    d = producer.get_draft(reels.brand, producer.make_draft(reels.brand, _plan_row("gemini_reel"), client=reels.claude))
    methods = []
    monkeypatch.setattr(telegram, "_call_files", lambda token, method, data, files: methods.append(method) or {})
    monkeypatch.setattr(telegram, "_call", lambda token, method, **kw: methods.append(method) or {"message_id": 1})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    REAL_SEND_DRAFT(reels.brand, d)
    assert methods == ["sendVideo", "sendMessage"]


def test_reel_command_respects_cap(reels, monkeypatch):
    from app import jobs
    brand_editor.update_config("skipperpacks", {"content_mix.max_reels_per_week": 0})
    brand = load_brand("skipperpacks")
    messages, started = [], []
    monkeypatch.setattr(telegram, "send_message", lambda b, t: messages.append(t))
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(a) or True)
    telegram.Bot(lambda: brand).handle(brand, {"chat": {"id": 12345}, "text": "/reel active brief"}, "12345")
    assert started == [] and "reel limit" in messages[-1]
