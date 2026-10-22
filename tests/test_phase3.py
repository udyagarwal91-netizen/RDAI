"""Phase 3 checks: queueing approved drafts, posting to Instagram/Facebook, retries, missed posts, pause,
plus the regenerate-with-a-note feature."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import brand_editor, producer, publisher, telegram
from app.brands import load_brand
from app.db import connect
from test_phase1 import brand  # noqa: F401 — shared fixture
from test_phase2 import _plan_row, studio  # noqa: F401 — shared fixture


class FakeMeta:
    """Stands in for graph.facebook.com and records every call."""

    def __init__(self, fail=None):
        self.calls, self.fail, self.n = [], fail or {}, 0

    def request(self, method, url, data=None, params=None, files=None, timeout=None):
        path = url.split("/v26.0/")[1]
        args = dict(data or params or {})
        self.calls.append((method, path, {k: v for k, v in args.items() if k != "access_token"}, bool(files)))
        for key, times in list(self.fail.items()):
            if key in path and times > 0:
                self.fail[key] -= 1
                return self._resp({"error": {"code": 2, "message": "Temporary problem"}}, 500)
        self.n += 1
        if path.endswith("/photos"):
            body = {"id": f"photo{self.n}", "post_id": f"page_post{self.n}"}
        elif path.endswith("/media"):
            body = {"id": f"container{self.n}"}
        elif path.endswith("/media_publish"):
            body = {"id": f"igmedia{self.n}"}
        elif path.endswith("/feed"):
            body = {"id": f"feedpost{self.n}"}
        elif args.get("fields") == "access_token":
            body = {"access_token": "PAGE_TOKEN"}
        elif args.get("fields") == "images":
            body = {"images": [{"width": 720, "source": "https://fb.cdn/small.jpg"},
                               {"width": 1080, "source": f"https://fb.cdn/{path}.jpg"}]}
        elif args.get("fields") == "status_code":
            body = {"status_code": "FINISHED"}
        elif args.get("fields") == "permalink":
            body = {"permalink": "https://instagram.com/p/abc"}
        elif args.get("fields") == "permalink_url":
            body = {"permalink_url": "https://facebook.com/post/1"}
        else:
            body = {}
        return self._resp(body)

    @staticmethod
    def _resp(body, status=200):
        return SimpleNamespace(status_code=status, headers={"content-type": "application/json"}, json=lambda: body)


@pytest.fixture
def live(studio, monkeypatch):
    brand_editor.update_config("skipperpacks", {"meta.facebook_page_id": "111", "meta.instagram_account_id": "222"})
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAtest123456789")
    publisher._page_tokens.clear()
    meta = FakeMeta()
    monkeypatch.setattr(publisher.requests, "request", meta.request)
    alerts = []
    studio.brand = load_brand("skipperpacks")
    studio.meta, studio.alerts = meta, alerts
    studio.notify = lambda text, buttons=None: alerts.append((text, buttons))
    return studio


def _approved(live, fmt="template_image", when=None):
    draft_id = producer.make_draft(live.brand, _plan_row(fmt), client=live.claude)
    if when is not None:
        with connect() as conn:
            conn.execute("UPDATE drafts SET scheduled_for = ? WHERE id = ?", (when.isoformat(), draft_id))
    producer.decide(live.brand, draft_id, "approve")
    return draft_id


def _due_now(draft_id):
    with connect() as conn:
        conn.execute("UPDATE posts SET scheduled_for = ? WHERE draft_id = ?",
                     ((publisher._now(load_brand("skipperpacks")) - timedelta(minutes=1)).isoformat(), draft_id))


def test_approving_queues_both_platforms_at_draft_time(live):
    draft_id = _approved(live)
    with connect() as conn:
        posts = conn.execute("SELECT platform, status, scheduled_for FROM posts WHERE draft_id = ?", (draft_id,)).fetchall()
        draft_time = conn.execute("SELECT scheduled_for FROM drafts WHERE id = ?", (draft_id,)).fetchone()[0]
    assert sorted(p["platform"] for p in posts) == ["facebook", "instagram"]
    assert all(p["status"] == "queued" and p["scheduled_for"][:16] == draft_time[:16] for p in posts)


def test_nothing_posts_before_its_time(live):
    _approved(live)  # planned for 2099
    assert publisher.publish_due(live.brand, live.notify) == 0 and live.meta.calls == []


def test_single_image_goes_to_instagram_via_hidden_facebook_photo(live):
    draft_id = _approved(live)
    _due_now(draft_id)
    assert publisher.publish_due(live.brand, live.notify) == 2
    paths = [(m, p) for m, p, _, _ in live.meta.calls]
    assert ("POST", "111/photos") in paths  # uploaded to the Page
    unpublished = next(a for m, p, a, f in live.meta.calls if p == "111/photos" and a.get("published") == "false")
    assert unpublished is not None
    container = next(a for m, p, a, f in live.meta.calls if p == "222/media")
    assert container["image_url"].startswith("https://fb.cdn/photo") and "#SkipperPacks" in container["caption"]
    assert ("POST", "222/media_publish") in paths
    fb_post = next(a for m, p, a, f in live.meta.calls if p == "111/photos" and a.get("published") == "true")
    assert "#SkipperPacks" in fb_post["caption"]
    assert producer.get_draft(live.brand, draft_id)["status"] == "published"
    assert any("Posted draft" in t and "instagram.com/p/abc" in t for t, _ in live.alerts)


def test_carousel_posts_as_carousel_on_both(live):
    draft_id = _approved(live, "carousel")
    _due_now(draft_id)
    publisher.publish_due(live.brand, live.notify)
    children = [a for m, p, a, f in live.meta.calls if p == "222/media" and a.get("is_carousel_item") == "true"]
    parent = next(a for m, p, a, f in live.meta.calls if p == "222/media" and a.get("media_type") == "CAROUSEL")
    assert len(children) == 4 and len(parent["children"].split(",")) == 4
    feed = next(a for m, p, a, f in live.meta.calls if p == "111/feed")
    assert json.loads(feed["attached_media[3]"])["media_fbid"].startswith("photo")
    uploads = [1 for m, p, a, f in live.meta.calls if p == "111/photos"]
    assert len(uploads) == 4  # the hidden photos are uploaded once and reused for both platforms


def test_failure_retries_then_alerts_with_button(live):
    live.meta.fail = {"222/media_publish": 99}
    draft_id = _approved(live)
    for _ in range(3):
        _due_now(draft_id)
        publisher.publish_due(live.brand, live.notify)
    with connect() as conn:
        ig = conn.execute("SELECT * FROM posts WHERE draft_id = ? AND platform = 'instagram'", (draft_id,)).fetchone()
    assert ig["status"] == "failed" and ig["attempts"] == 3 and "Temporary problem" in ig["last_error"]
    text, buttons = next(a for a in live.alerts if a[0].startswith("❌"))
    assert buttons == [("🔁 Try again", f"p:{ig['id']}")]
    publisher.retry_now(live.brand, ig["id"])
    live.meta.fail = {}
    publisher.publish_due(live.brand, live.notify)
    with connect() as conn:
        assert conn.execute("SELECT status FROM posts WHERE id = ?", (ig["id"],)).fetchone()[0] == "published"


def test_very_late_posts_ask_first(live):
    draft_id = _approved(live)
    with connect() as conn:
        conn.execute("UPDATE posts SET scheduled_for = ? WHERE draft_id = ?",
                     ((publisher._now(live.brand) - timedelta(hours=10)).isoformat(), draft_id))
    assert publisher.publish_due(live.brand, live.notify) == 0 and live.meta.calls == []
    text, buttons = live.alerts[0]
    assert "wasn't running" in text and buttons[0][0] == "📤 Post now"


def test_pause_stops_everything(live):
    draft_id = _approved(live)
    _due_now(draft_id)
    publisher.set_paused(live.brand, True)
    assert publisher.publish_due(live.brand, live.notify) == 0
    publisher.set_paused(live.brand, False)
    assert publisher.publish_due(live.brand, live.notify) == 2


def test_unscheduled_image_gets_next_free_slot(live):
    from app import jobs
    style = producer.find_style(live.brand, "active brief")
    draft_id = producer.make_draft(live.brand, on_demand=producer.on_demand_post(live.brand, style["id"]), client=live.claude)
    when = producer.decide(live.brand, draft_id, "approve")
    assert when > publisher._now(live.brand)
    assert when.strftime("%a %H:%M") in {"Mon 19:30", "Wed 19:30", "Thu 13:00", "Sat 11:00", "Sun 19:00"}


def test_too_long_caption_is_refused_clearly(live):
    draft_id = _approved(live)
    with connect() as conn:
        conn.execute("UPDATE drafts SET caption = ? WHERE id = ?", ("x" * 2300, draft_id))
    with pytest.raises(publisher.PublishError, match="2300 characters"):
        publisher.publish_instagram(live.brand, producer.get_draft(live.brand, draft_id))


def test_expired_token_message(live):
    body = {"error": {"code": 190, "message": "Session expired"}}
    assert "no longer valid" in publisher._explain(body, 400)


def test_telegram_queue_pause_and_post_buttons(live, monkeypatch):
    messages, calls = [], []
    monkeypatch.setattr(telegram, "send_message", lambda b, text: messages.append(text))
    monkeypatch.setattr(telegram, "_call", lambda token, method, **kw: calls.append(method) or {})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    bot = telegram.Bot(lambda: live.brand)
    draft_id = _approved(live)
    bot.handle(live.brand, {"chat": {"id": 12345}, "text": "/queue"}, "12345")
    assert f"draft #{draft_id}" in messages[-1]
    bot.handle(live.brand, {"chat": {"id": 12345}, "text": "/pause"}, "12345")
    assert publisher.is_paused(live.brand) and "PAUSED" in telegram.format_queue(live.brand)
    bot.handle(live.brand, {"chat": {"id": 12345}, "text": "/resume"}, "12345")
    assert not publisher.is_paused(live.brand)
    with connect() as conn:
        post_id = conn.execute("SELECT id FROM posts WHERE draft_id = ? LIMIT 1", (draft_id,)).fetchone()[0]
        conn.execute("UPDATE posts SET status = 'missed' WHERE id = ?", (post_id,))
    bot.handle_button(live.brand, {"id": "q", "from": {"id": 12345}, "data": f"p:{post_id}"}, "12345")
    with connect() as conn:
        assert conn.execute("SELECT status FROM posts WHERE id = ?", (post_id,)).fetchone()[0] == "queued"


def test_regenerate_asks_for_a_note_and_uses_it(studio, monkeypatch):
    from app import jobs
    from app.db import get_state

    draft_id = producer.make_draft(studio.brand, _plan_row("gemini_image"), client=studio.claude)
    messages, started = [], []
    monkeypatch.setattr(telegram, "send_message", lambda b, text: messages.append(text))
    monkeypatch.setattr(telegram, "_call", lambda token, method, **kw: {})
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: started.append((action, kw)) or True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    bot = telegram.Bot(lambda: studio.brand)
    bot.handle_button(studio.brand, {"id": "q", "from": {"id": 12345}, "data": f"r:{draft_id}"}, "12345")
    assert "What should be different" in messages[-1]
    assert get_state("skipperpacks", "awaiting_regen") == str(draft_id)
    bot.handle(studio.brand, {"chat": {"id": 12345}, "text": "gym bag flat-lay, no model"}, "12345")
    assert started == [("regen", {"draft_id": draft_id, "note": "gym bag flat-lay, no model"})]

    # The note reaches both the caption writer and the image maker.
    producer.remake(studio.brand, draft_id, note="gym bag flat-lay, no model", client=studio.claude)
    writer_call = [c for c in studio.claude.calls if "caption" in c["output_config"]["format"]["schema"]["properties"]][-1]
    assert "gym bag flat-lay, no model" in json.dumps(writer_call["messages"])
    assert "The owner asked for: gym bag flat-lay, no model" in studio.gemini[-1]


def test_regenerate_skip_makes_plain_new_version(studio, monkeypatch):
    from app import jobs

    draft_id = producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    started = []
    monkeypatch.setattr(telegram, "send_message", lambda b, text: None)
    monkeypatch.setattr(telegram, "_call", lambda token, method, **kw: {})
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: started.append(kw) or True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    bot = telegram.Bot(lambda: studio.brand)
    bot.handle_button(studio.brand, {"id": "q", "from": {"id": 12345}, "data": f"r:{draft_id}"}, "12345")
    bot.handle(studio.brand, {"chat": {"id": 12345}, "text": "/skip"}, "12345")
    assert started == [{"draft_id": draft_id, "note": ""}]


def test_unexpected_meta_answer_is_retried_not_stuck(live, monkeypatch):
    draft_id = _approved(live)
    _due_now(draft_id)
    monkeypatch.setitem(publisher.PLATFORMS, "facebook", lambda b, d: {}["id"])  # Meta answered without an id
    publisher.publish_due(live.brand, live.notify)
    with connect() as conn:
        fb = conn.execute("SELECT * FROM posts WHERE draft_id = ? AND platform = 'facebook'", (draft_id,)).fetchone()
    assert fb["status"] == "queued" and "unexpected" in fb["last_error"]  # will be retried


def test_interrupted_post_is_flagged_not_reposted(live):
    draft_id = _approved(live)
    old = (publisher._now(live.brand) - timedelta(minutes=30)).isoformat()
    with connect() as conn:
        conn.execute("UPDATE posts SET status = 'publishing', published_at = ? WHERE draft_id = ?", (old, draft_id))
    publisher.publish_due(live.brand, live.notify)
    with connect() as conn:
        statuses = {r[0] for r in conn.execute("SELECT status FROM posts WHERE draft_id = ?", (draft_id,))}
    assert statuses == {"failed"} and live.meta.calls == []
    assert any("interrupted" in t and b[0][0] == "📤 Post now" for t, b in live.alerts)
