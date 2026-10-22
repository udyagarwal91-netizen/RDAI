"""Phase 4 checks: collecting Instagram/Facebook numbers, the report, lessons, and feeding them into planning."""

import json
from datetime import datetime, timedelta

import pytest

from app import insights, jobs, planner, producer, publisher, telegram
from app.db import connect
from test_phase1 import brand  # noqa: F401 — shared fixture
from test_phase2 import StudioClaude, _plan_row, studio  # noqa: F401
from test_phase3 import FakeMeta, _approved, _due_now, live  # noqa: F401


class InsightMeta(FakeMeta):
    """FakeMeta that also answers insights; rejects one retired metric like the real API does."""

    def __init__(self, reach=1000, saves=40):
        super().__init__()
        self.reach, self.saves = reach, saves

    def request(self, method, url, data=None, params=None, files=None, timeout=None):
        path = url.split("/v26.0/")[1]
        args = dict(params or data or {})
        if path.endswith("/insights"):
            metrics = args["metric"].split(",")
            if "follows" in metrics:  # pretend Meta retired this one
                return self._resp({"error": {"code": 100, "message": "(#100) metric follows is not valid"}}, 400)
            values = {"reach": self.reach, "likes": 50, "comments": 5, "saved": self.saves, "shares": 12,
                      "views": 3000, "total_interactions": 107, "profile_visits": 9,
                      "post_total_media_view_unique": self.reach // 2, "post_media_view": 900, "post_clicks": 14}
            return self._resp({"data": [{"name": m, "period": "lifetime", "values": [{"value": values.get(m, 0)}]}
                                        for m in metrics]})
        if args.get("fields", "").startswith("reactions"):
            return self._resp({"reactions": {"summary": {"total_count": 21}}, "comments": {"summary": {"total_count": 2}},
                               "shares": {"count": 3}})
        return super().request(method, url, data, params, files, timeout)


@pytest.fixture
def results(live, monkeypatch):
    insights._rejected.clear()
    meta = InsightMeta()
    monkeypatch.setattr(publisher.requests, "request", meta.request)
    draft_id = _approved(live)
    _due_now(draft_id)
    publisher.publish_due(live.brand, live.notify)
    live.meta, live.draft_id = meta, draft_id
    return live


def test_collect_reads_instagram_and_facebook_numbers(results):
    assert insights.collect(results.brand) == 2
    rows = {r["platform"]: r for r in insights.latest(results.brand)}
    ig, fb = rows["instagram"], rows["facebook"]
    assert (ig["reach"], ig["saves"], ig["shares"], ig["impressions"], ig["profile_visits"]) == (1000, 40, 12, 3000, 9)
    assert ig["interactions"] == 50 + 5 + 40 + 12 and abs(ig["rate"] - 0.107) < 1e-9
    assert (fb["reach"], fb["likes"], fb["comments"], fb["shares"], fb["link_clicks"]) == (500, 21, 2, 3, 14)
    assert "follows" in insights._rejected  # the retired metric was skipped, the rest still collected


def test_report_groups_and_ranks(results):
    insights.collect(results.brand)
    rep = insights.report(results.brand, 7)
    assert rep["by_format"][0]["name"] == "Brand image" and rep["by_format"][0]["posts"] == 2
    assert rep["best"][0]["platform"] == "instagram"  # higher engagement than facebook here
    assert set(rep["totals"]) == {"instagram", "facebook"}


def test_lessons_need_three_posts_then_feed_the_planner(results, monkeypatch):
    insights.collect(results.brand)
    assert insights.make_lessons(results.brand, client=results.claude) is None  # only 2 results so far
    for _ in range(2):
        d = _approved(results)
        _due_now(d)
        publisher.publish_due(results.brand, results.notify)
    insights.collect(results.brand)

    class LessonClaude(StudioClaude):
        def _create(self, **kw):
            self.calls.append(kw)
            from types import SimpleNamespace
            answer = {"headline": "Brand images on weekday evenings get saved most",
                      "worked": ["Product posts with price got 40 saves"], "try_next": ["Try a fabric-care carousel"]}
            usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)
            return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                                   content=[SimpleNamespace(type="text", text=json.dumps(answer))])
    fake = LessonClaude()
    les = insights.make_lessons(results.brand, client=fake)
    assert les["headline"].startswith("Brand images")
    assert "saves 40" in fake.calls[0]["messages"][0]["content"]
    summary = planner.performance_summary(results.brand)
    assert "Brand images on weekday evenings" in summary and "By format" in summary


def test_stats_message_and_command(results, monkeypatch):
    insights.collect(results.brand)
    text = telegram.format_stats(results.brand)
    assert "last 7 days" in text and "Best posts" in text and "instagram.com/p/abc" in text
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda b, t: sent.append(t))
    telegram.Bot(lambda: results.brand).handle(results.brand, {"chat": {"id": 12345}, "text": "/stats"}, "12345")
    assert "Best posts" in sent[0]


def test_no_results_yet_message(brand):
    assert "No results yet" in telegram.format_stats(brand)
    assert planner.performance_summary(brand).startswith("No post results yet")


def test_performance_page(results, monkeypatch, sandbox):
    import re
    import app.web as web
    from app import secrets_store

    insights.collect(results.brand)
    secrets_store.save("FLASK_SECRET_KEY", "x" * 64)
    c = web.create_app().test_client()
    page = c.get("/setup-password").get_data(as_text=True)
    c.post("/setup-password", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1),
                                     "password": "goodpass1", "confirm": "goodpass1"})
    html = c.get("/performance").get_data(as_text=True)
    assert "Every post" in html and "10.7%" in html and "Brand image" in html
