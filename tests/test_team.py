"""Agent team checks (Phase B): playbooks, briefs, decisions, Researcher, Manager, weekly meeting, Team pages.
Claude and Meta are stand-ins; nothing touches the network."""

import json
from types import SimpleNamespace

import pytest

from app import claude, jobs, planner, publisher, shopify, team, telegram
from app.brands import load_brand
from app.team import manager, meeting, researcher
from test_control_center import _token, client  # noqa: F401 — shared fixture
from test_phase1 import _plan, brand  # noqa: F401 — shared fixture

USAGE = SimpleNamespace(input_tokens=1000, output_tokens=300, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _answer_for(schema: dict, calls: int) -> dict:
    props = schema["properties"]
    if "posts" in props:  # the planner
        items = props["posts"]["items"]["properties"]
        return _plan(items["slot"]["enum"], items["styles"]["items"]["enum"], 2, fmt="carousel")
    if "competitors" in props:
        return {"competitors": [{"name": "Rival Co", "instagram": "@Rival_Co", "website": "rival.in",
                                 "price_note": "3-pack ₹599 (₹200/pc), rival.in", "why": "same packs"},
                                {"name": "No Insta", "instagram": "", "website": "x.in", "price_note": "?", "why": "?"}]}
    if "insights" in props:
        return {"headline": "Comfort-proof reels beat offers this month", "insights": [
            {"insight": "Reels showing a stretch test get 2× engagement", "evidence": "@rival post 2.3× avg",
             "confidence": "strong", "use_it": "Plan one stretch-test reel"}],
            "hooks_to_try": ["Elastic digging in by 4 pm?"], "formats_working": ["UGC-style reels"],
            "dates_coming": ["Wedding season"], "customer_words": ["no rashes"], "watch_out": []}
    if "verdict" in props:
        return {"verdict": "revise" if calls < 99 else "good", "notes": ["Add the stretch-test reel"],
                "revision_request": "Swap Thursday's image for a stretch-test reel."}
    if "priorities" in props:
        return {"headline": "Push the 3-pack with comfort proof", "priorities": [
            {"priority": "Comfort-proof reels", "why": "Research strong signal"}],
            "plan_instructions": "Two comfort-proof reels, one value carousel.",
            "creator_notes": "Lead with the elastic problem.",
            "decisions": [{"decision": "Test stretch-test reels", "reason": "market brief",
                           "expected_result": "saves +30%", "check_after_days": 14}],
            "reviews": [{"decision_id": 1, "verdict": "worked", "what_we_learned": "Reels win"}],
            "asks_for_owner": []}
    if "summary" in props and "wins" in props:
        return {"summary": "Good week.", "wins": ["Reels up"], "problems": [], "decided": ["More reels"],
                "needs_from_owner": ["Confirm the daily ad budget"]}
    raise AssertionError(f"unexpected schema {list(props)}")


class TeamClaude:
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools"):  # web research
            assert kwargs["tools"][0]["type"] == "web_search_20260209"
            usage = SimpleNamespace(**vars(USAGE), server_tool_use=SimpleNamespace(web_search_requests=3))
            return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                                   content=[SimpleNamespace(type="text", text="Reels with proof trend (source: x.com)")])
        data = _answer_for(kwargs["output_config"]["format"]["schema"], len(self.calls))
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=USAGE,
                               content=[SimpleNamespace(type="text", text=json.dumps(data))])


@pytest.fixture
def crew(brand, monkeypatch):
    shopify.sync_products(brand)
    fake = TeamClaude()
    monkeypatch.setattr(claude, "client_for", lambda b: fake)
    said = []
    monkeypatch.setattr(telegram, "send_buttons", lambda b, text, buttons=None: said.append(text))
    monkeypatch.setattr(telegram, "send_message", lambda b, text: said.append(text))
    return SimpleNamespace(brand=load_brand("skipperpacks"), claude=fake, said=said)


def test_playbooks_default_custom_and_reset(crew):
    b = crew.brand
    assert "Hook formulas" in team.skill(b, "creator") and not team.is_custom(b, "creator")
    team.save_skill(b, "creator", "Always use Hinglish.")
    assert team.skill(load_brand("skipperpacks"), "creator").strip() == "Always use Hinglish."
    team.save_skill(b, "creator", "")
    assert not team.is_custom(b, "creator")


def test_competitor_handles_are_cleaned(crew):
    got = team.set_competitors(crew.brand, "@XYXX_crew, https://instagram.com/damensch/?hl=en\nbad handle!!")
    assert got == ["xyxx_crew", "damensch", "bad"]  # "handle!!" isn't a valid Instagram name


def test_competitor_scan_uses_official_business_discovery(crew, monkeypatch):
    from app import brand_editor
    brand_editor.update_config("skipperpacks", {"meta.instagram_account_id": "222"})
    b = load_brand("skipperpacks")
    seen = []
    media = [{"like_count": 100, "comments_count": 10, "media_type": "VIDEO", "media_product_type": "REELS",
              "timestamp": "2026-11-01T10:00:00+0000", "permalink": "https://ig/p/1", "caption": "Stretch test"},
             {"like_count": 20, "comments_count": 0, "media_type": "IMAGE", "timestamp": "2026-10-25T10:00:00+0000",
              "permalink": "https://ig/p/2", "caption": "Sale"}]

    def fake_call(method, url, token, **params):
        seen.append((url, params["fields"]))
        return {"business_discovery": {"name": "Rival", "followers_count": 5000, "media": {"data": media}}}
    monkeypatch.setattr(publisher, "page_token", lambda brand: "PAGE")
    monkeypatch.setattr(publisher, "_call", fake_call)
    c = researcher.scan_competitor(b, "rival")
    assert seen[0][0].endswith("/222") and "business_discovery.username(rival)" in seen[0][1]
    assert c["formats"]["reel"] == 1 and c["top_posts"][0]["vs_avg"] > 1.5 and c["posts_per_week"] == 1.0


def test_research_brief_is_stored_with_sources_and_cost(crew):
    r = researcher.run(crew.brand)
    assert r["cost_usd"] > 0.03  # includes 3 web searches at 1 US cent each
    b = team.latest(crew.brand, "market")
    assert b["title"].startswith("Comfort-proof") and "@rival post 2.3× avg" in b["body"]
    assert b["data"]["searches"] == 3
    prompt = crew.claude.calls[-1]["messages"][0]["content"]
    assert "GUARDRAILS" in prompt and "Reels with proof trend" in prompt  # goals + web notes reach the brief


def test_manager_direction_logs_decisions_and_reviews_old_ones(crew):
    old = team.add_decision(crew.brand, None, "Old idea", "why", "what", check_after_days=3)
    from app.db import connect
    with connect() as conn:
        conn.execute("UPDATE team_decisions SET check_on = '2000-01-01' WHERE id = ?", (old,))
    d = manager.direct(crew.brand)
    assert d["data"]["plan_instructions"].startswith("Two comfort-proof")
    decided = {x["decision"]: x for x in team.decisions(crew.brand)}
    assert decided["Old idea"]["status"] == "worked" and decided["Test stretch-test reels"]["status"] == "open"
    manager.tell_owner(crew.brand, d)
    assert "🧠 <b>Manager</b>" in crew.said[-1]


def test_weekly_meeting_runs_every_step_and_revises_plan_once(crew, monkeypatch):
    monkeypatch.setattr(jobs, "prepare_for_planning", lambda slug: None)
    notes = []
    real = planner.make_plan
    monkeypatch.setattr(planner, "make_plan", lambda b, note="", **k: notes.append(note) or real(b, note=note, **k))
    msg = meeting.run("skipperpacks")
    assert "posts planned" in msg and "market brief written" in msg and "direction set" in msg
    assert "plan revised" in msg
    assert len(notes) == 2 and notes[0].startswith("Two comfort-proof") and "stretch-test reel" in notes[1]
    kinds = [b["kind"] for b in team.briefs(crew.brand)]
    assert "market" in kinds and "direction" in kinds
    assert any("Market brief" in s for s in crew.said) and any("This week" in s for s in crew.said)


def test_meeting_still_plans_when_research_fails(crew, monkeypatch):
    monkeypatch.setattr(jobs, "prepare_for_planning", lambda slug: None)
    monkeypatch.setattr(researcher, "run", lambda b: (_ for _ in ()).throw(claude.ClaudeError("busy")))
    msg = meeting.run("skipperpacks")
    assert "market research skipped (busy)" in msg and "posts planned" in msg


def test_weekly_report(crew):
    r = manager.weekly_report(crew.brand)
    assert "Confirm the daily ad budget" in r["body"] and "Blended ROAS" in r["body"]
    assert "Weekly report" in crew.said[-1]


def test_team_and_research_pages(client, monkeypatch):
    b = load_brand("skipperpacks")
    team.post_brief(b, "researcher", "market", "Reels win", "Body text", {"competitors": [
        {"handle": "rival", "followers": 10, "posts_per_week": 3, "avg_engagement": 5,
         "formats": {"reel": 2, "carousel": 1, "image": 0, "video": 0},
         "top_posts": [{"type": "reel", "likes": 9, "vs_avg": 2.0, "url": "https://ig/p/1", "caption": "x"}]}]})
    page = client.get("/team").get_data(as_text=True)
    assert "Reels win" in page and "Skill playbooks" in page and "Full team meeting" in page
    client.post("/team/skill/smo", data={"csrf": _token(client), "text": "Be brief."})
    assert team.skill(load_brand("skipperpacks"), "smo").strip() == "Be brief."
    client.post("/research", data={"csrf": _token(client), "competitors": "@rival"})
    page = client.get("/research").get_data(as_text=True)
    assert "@rival" in page and "2.0× their average" in page
    assert "Latest from your team" in client.get("/").get_data(as_text=True)


def test_chat_can_read_team_briefs(crew):
    from app import assistant
    assert "hasn't written" in assistant.run_tool(crew.brand, "team_briefs", {})
    team.post_brief(crew.brand, "manager", "direction", "Push 3-packs", "Body")
    assert "Push 3-packs" in assistant.run_tool(crew.brand, "team_briefs", {})


def test_meeting_survives_unexpected_errors_and_names_them(crew, monkeypatch):
    monkeypatch.setattr(jobs, "prepare_for_planning", lambda slug: None)
    monkeypatch.setattr(researcher, "run", lambda b: {}["boom"])  # an unexpected KeyError
    monkeypatch.setattr(manager, "direct", lambda b: 1 / 0)
    msg = meeting.run("skipperpacks")
    assert "market research skipped (unexpected problem: KeyError" in msg
    assert "manager skipped (unexpected problem: ZeroDivisionError" in msg and "posts planned" in msg


def test_agent_answer_formats_have_no_unsupported_limits():
    from app.team import campaigns, finance
    text = json.dumps([manager.DIRECTION_SCHEMA, manager.REPORT_SCHEMA, finance.BRIEF_SCHEMA, campaigns.SCHEMA,
                       researcher.BRIEF_SCHEMA, researcher.REVIEW_SCHEMA])
    for word in ("maxItems", "minItems", "maxLength", "minimum", "maximum"):
        assert word not in text



def test_researcher_finds_competitors_itself_and_logs_its_work(crew, monkeypatch):
    scanned = []
    monkeypatch.setattr(researcher, "scan_competitor", lambda b, h: scanned.append(h) or
                        {"handle": h, "formats": {}, "top_posts": [], "posts": []})
    team.set_competitors(crew.brand, "@my_pick")
    researcher.run(crew.brand)
    found = researcher.found_competitors(crew.brand)
    assert found["list"][0]["instagram"] == "rival_co" and found["found_at"]
    assert scanned == ["my_pick", "rival_co"]  # yours first, then the ones it found (no empty handles)
    prompt = crew.claude.calls[-1]["messages"][0]["content"]
    assert "Rival Co" in prompt and "₹200/pc" in prompt
    log = team.work_log(crew.brand, "researcher")
    assert [w["task"] for w in log] == ["Finding competitors", "Market research"]  # newest first; found inside research
    assert all(w["status"] == "done" and w["seconds"] is not None for w in log)
    assert log[0]["result"].startswith("Found 2 competitors") and log[1]["cost_usd"] > 0
    researcher.run(crew.brand)  # within a month: no new search
    assert [w["task"] for w in team.work_log(crew.brand, "researcher")].count("Finding competitors") == 1


def test_work_log_records_failures_and_stats(crew):
    with pytest.raises(ZeroDivisionError):
        with team.working(crew.brand, "smo", "Daily ad check"):
            1 / 0
    with team.working(crew.brand, "smo", "Daily ad check") as job:
        job["cost_usd"] = 0.5
    w = team.work_stats(crew.brand, "smo", 7)
    assert w["tasks"] == 2 and w["failed"] == 1 and w["cost_usd"] == 0.5 and w["by_task"][0]["share"] == 100
    assert team.work_log(crew.brand, "smo")[1]["result"].startswith("ZeroDivisionError")
    assert team.now_working(crew.brand) == {}


def test_planner_and_creator_work_is_logged(crew):
    planner.make_plan(crew.brand, client=crew.claude)
    entry = team.work_log(crew.brand, "planner")[0]
    assert entry["task"] == "Weekly content plan" and "posts planned" in entry["result"]


def test_agent_pages(client):
    b = load_brand("skipperpacks")
    with team.working(b, "researcher", "Market research") as job:
        job["result"] = "Brief written"
    page = client.get("/team/agent/researcher").get_data(as_text=True)
    assert "Work log" in page and "Brief written" in page and "Where the time goes" in page
    for key in team.AGENTS:
        assert client.get(f"/team/agent/{key}").status_code == 200
    assert client.get("/team/agent/nobody").status_code == 404
    assert "Your six agents" in client.get("/team").get_data(as_text=True)
    assert "Competitors the Researcher found" in client.get("/research").get_data(as_text=True)
