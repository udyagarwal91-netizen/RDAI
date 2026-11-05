"""Content Calendar checks: a month plan with Stories, pillars and the spreadsheet columns; weekly adjustments that
keep the rest of the month; item actions; the Calendar page. Claude is a stand-in."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import claude, content_calendar, planner, producer, shopify, telegram
from app.brands import load_brand
from test_control_center import _token, client  # noqa: F401 — shared fixture
from test_phase1 import brand  # noqa: F401 — shared fixture

START = date(2099, 1, 1)  # a Thursday


class CalClaude:
    """Fills every feed slot, uses two story times, and picks pillars."""

    def __init__(self, hook="₹73 a piece. Factory price."):
        self.calls, self.hook = [], hook
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        props = kwargs["output_config"]["format"]["schema"]["properties"]
        if "posts" in props:
            slots = props["posts"]["items"]["properties"]["slot"]["enum"]
            styles = props["posts"]["items"]["properties"]["styles"]["items"]["enum"]
            story_slots = props["stories"]["items"]["properties"]["story_slot"]["enum"]
            posts = [{"slot": s, "theme": "product", "format": "gemini_reel" if i % 4 == 0 else "carousel",
                      "styles": [styles[i % len(styles)]], "idea": f"Idea {i}", "hook": f"Hook {i}", "why": "w",
                      "pillar": ["Price Math", "Comfort"][i % 2], "goal": "sales", "tagline": "Factory price.",
                      "cta": "Shop at skipperpacks.com", "direction": "Kinetic type, end card.",
                      "boost": i == 1, "boost_budget": 150} for i, s in enumerate(slots)]
            stories = [{"story_slot": story_slots[0], "story_type": "poll", "styles": [], "idea": "Brief or trunk?",
                        "hook": "Brief or Trunk?", "sticker": "Poll: BRIEF / TRUNK", "pillar": "Comfort",
                        "goal": "engagement", "why": "w"},
                       {"story_slot": story_slots[0], "story_type": "quiz", "styles": [], "idea": "dup",
                        "hook": "dup", "sticker": "", "pillar": "Comfort", "goal": "engagement", "why": "w"},
                       {"story_slot": story_slots[3], "story_type": "festival", "styles": [], "idea": "Greeting",
                        "hook": "Happy festival", "sticker": "", "pillar": "Trust", "goal": "trust", "why": "w"}]
            answer = {"summary": "Price math and comfort month.", "posts": posts, "stories": stories,
                      "pillars": [{"name": "Price Math", "why": "value wins"}, {"name": "Comfort", "why": "core"}]}
        else:  # redo one item
            answer = {"idea": "New idea", "hook": self.hook, "tagline": "New tag", "cta": "Shop now",
                      "direction": "Macro shots", "sticker": ""}
        usage = SimpleNamespace(input_tokens=2000, output_tokens=900, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(answer))])


@pytest.fixture
def cal(brand, monkeypatch):
    shopify.sync_products(brand)
    fake = CalClaude()
    monkeypatch.setattr(claude, "client_for", lambda b: fake)
    b = load_brand("skipperpacks")
    result = planner.make_plan(b, start=START, days=31)
    return SimpleNamespace(brand=b, claude=fake, result=result)


def test_month_plan_has_stories_pillars_and_columns(cal):
    feed = [r for r in cal.result["rows"] if r["format"] != "story"]
    stories = [r for r in cal.result["rows"] if r["format"] == "story"]
    assert len(feed) == len(planner.plan_slots(cal.brand, START, 31)) > 15
    assert len(stories) == 2  # the double-booked story time was dropped
    n = json.loads(feed[1]["notes"])
    assert n["pillar"] == "Comfort" and n["tagline"] == "Factory price." and n["boost"] and n["boost_budget"] == 150
    assert json.loads(stories[0]["notes"])["sticker"] == "Poll: BRIEF / TRUNK"
    assert planner.current_pillars(cal.brand)[0]["name"] == "Price Math"
    prompt = cal.claude.calls[-1]["messages"][0]["content"]
    assert "STORIES" in prompt and "decide 4–6 content pillars" in prompt and "names from the product list" in prompt
    reels = sum(r["format"] == "gemini_reel" for r in feed)
    assert reels <= int(cal.brand.config["content_mix"]["max_reels_per_week"]) * 31 // 7


def test_weekly_plan_keeps_the_rest_of_the_month_and_pillars(cal):
    before = content_calendar.items(cal.brand, START + timedelta(days=10), START + timedelta(days=30))
    planner.make_plan(cal.brand, start=START, days=7)
    after = content_calendar.items(cal.brand, START + timedelta(days=10), START + timedelta(days=30))
    assert [i["id"] for i in after] == [i["id"] for i in before]  # untouched outside the week
    prompt = cal.claude.calls[-1]["messages"][0]["content"]
    assert "already set for this month" in prompt and "Price Math" in prompt


def test_stories_are_not_drafted_yet(cal):
    rows = producer.due_plan_rows(cal.brand, days=40000, limit=500)
    assert rows and all(r["format"] != "story" for r in rows)


def test_month_view_counts_and_status(cal):
    m = content_calendar.month(cal.brand, 2099, 1)
    types = dict(m["by_type"])
    assert types["Story"] == 2 and types["Reel"] >= 1 and types["Post"] >= 1
    assert dict(m["by_pillar"])["Comfort"] >= 2 and len(m["weeks"]) >= 5
    feed = [i for i in m["items"] if i["type"] != "Story"]
    assert feed[0]["status_label"] == "Idea" and m["boosts"]
    assert next(i for i in m["items"] if i["type"] == "Story")["status_label"] == "Idea · story"


def _first_post(b):
    return next(i for i in content_calendar.items(b, START, START) if i["type"] != "Story")


def test_item_actions(cal, monkeypatch):
    it = _first_post(cal.brand)
    content_calendar.approve_idea(cal.brand, it["id"])
    it2 = _first_post(cal.brand)
    assert it2["status_label"] == "Idea ✅"
    warns = content_calendar.edit(cal.brand, it["id"], {"hook": "Now only ₹9", "tagline": "My tag"})
    assert any("₹9" in w for w in warns)  # a price not in Shopify is flagged
    edited = _first_post(cal.brand)
    assert edited["hook"] == "Now only ₹9" and edited["tagline"] == "My tag"
    assert "edited by the owner" in content_calendar.week_context(cal.brand, START)
    content_calendar.redo(cal.brand, it["id"], "funnier")
    redone = _first_post(cal.brand)
    assert redone["hook"] == cal.claude.hook and not redone["idea_ok"]
    assert "funnier" in cal.claude.calls[-1]["messages"][0]["content"]
    content_calendar.skip(cal.brand, it["id"])
    assert next(i for i in content_calendar.items(cal.brand, START, START) if i["id"] == it["id"])[
        "status_label"] == "Skipped"


def test_month_message_on_telegram_is_short(cal):
    rows = planner.upcoming_plan(cal.brand)
    text = telegram.format_plan(cal.brand, "s", rows)
    assert "Calendar" in text and "Stories" in text and text.count("💡") <= 9


def test_calendar_page(client, monkeypatch):
    page = client.get("/calendar?m=2099-01").get_data(as_text=True)
    assert "January 2099" in page and "Plan next month" in page


def test_calendar_page_with_items_and_actions(client, monkeypatch, brand):
    shopify.sync_products(brand)
    fake = CalClaude()
    monkeypatch.setattr(claude, "client_for", lambda b: fake)
    planner.make_plan(load_brand("skipperpacks"), start=START, days=31)
    page = client.get("/calendar?m=2099-01").get_data(as_text=True)
    assert "Price Math" in page and "Poll: BRIEF / TRUNK" in page and "🎬" in page and "⭕" in page
    page = client.get("/calendar?m=2099-01&type=Story").get_data(as_text=True)
    assert "(2 items)" in page
    item = _first_post(load_brand("skipperpacks"))
    client.post(f"/calendar/item/{item['id']}/approve", data={"csrf": _token(client)})
    assert _first_post(load_brand("skipperpacks"))["idea_ok"]
    from app import jobs
    started = []
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: started.append((action, kw)) or True)
    client.post(f"/calendar/item/{item['id']}/make", data={"csrf": _token(client)})
    client.post("/action/month_plan", data={"csrf": _token(client), "which": "now"})
    assert started == [("draft_item", {"plan_id": item["id"]}), ("month_plan", {"which": "now"})]
