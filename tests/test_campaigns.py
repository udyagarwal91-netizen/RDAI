"""Phase C checks: Creator sees the team's guidance; Planner's campaign plan stays inside the owner's limits;
ad proposals are checked against the guardrails."""

import json
from types import SimpleNamespace

import pytest

from app import claude, goals, meta_ads, telegram, writer
from app.brands import load_brand
from app.db import connect, set_state
from app.team import campaigns, post_brief, proposals
from test_phase1 import brand  # noqa: F401 — shared fixture
from test_phase2 import _plan_row, studio  # noqa: F401 — shared fixture

STRUCTURE = {"read_at": "2026-11-04T08:00:00", "adsets": [
    {"id": "AS1", "name": "Prospecting men", "status": "ACTIVE", "daily_budget": 400.0, "campaign_id": "C1",
     "campaign": "Sales", "objective": "OUTCOME_SALES"}],
    "ads": [{"id": "AD1", "name": "Reel 1", "status": "ACTIVE", "adset_id": "AS1", "creative_id": "CR1"}]}


@pytest.fixture
def ads(brand):
    set_state("skipperpacks", "meta:ad_account", "1840946140217408")
    set_state("skipperpacks", "meta:structure", json.dumps(STRUCTURE))
    return load_brand("skipperpacks")


def test_creator_gets_manager_notes_and_market_hooks(studio):
    set_state("skipperpacks", "team:creator_notes", "Lead with the elastic problem.")
    post_brief(studio.brand, "researcher", "market", "x", "y", {"hooks_to_try": ["Elastic digging in?"],
                                                                "customer_words": ["no rashes"]})
    writer.write(studio.brand, {"format": "template_image", "theme": "product", "idea": "i", "hook": "h"},
                 [], client=studio.claude)
    call = studio.claude.calls[-1]
    texts = " ".join(c["text"] for c in call["messages"][0]["content"] if c["type"] == "text")
    assert "Lead with the elastic problem" in texts and "Elastic digging in?" in texts and "no rashes" in texts
    assert "Hook formulas" in call["system"][0]["text"]  # the Creator's playbook
    schema = call["output_config"]["format"]["schema"]
    assert {"hook_options", "hook_reason", "funnel_stage"} <= set(schema["required"])


def test_planner_follows_its_playbook(studio):
    call = next(c for c in studio.claude.calls if "posts" in c["output_config"]["format"]["schema"]["properties"])
    assert "Campaign architect" in call["system"][0]["text"]


def test_budget_raise_needs_good_roas_and_is_capped(ads):
    with pytest.raises(proposals.GuardrailError, match="blended ROAS"):
        proposals.check(ads, "budget", {"from": 400, "to": 450})
    goals.record(ads, {"blended_roas": 9}, "test")
    assert proposals.check(ads, "budget", {"from": 400, "to": 900})["to"] == 480  # max +20%
    # daily limit ₹500 total: only ₹100 of room beside the running ₹400
    assert proposals.check(ads, "retarget", {"daily_budget": 300, "audience": "cart_14d"})["daily_budget"] == 100


def test_month_cap_blocks_new_spend(ads):
    with connect() as conn:
        from datetime import date
        conn.execute("INSERT INTO ads_daily (brand, day, ad_id, spend) VALUES ('skipperpacks', ?, 'AD1', 20000)",
                     (date.today().replace(day=1).isoformat(),))
    with pytest.raises(proposals.GuardrailError, match="month"):
        proposals.check(ads, "retarget", {"daily_budget": 50})


class PlanClaude:
    def __init__(self, data):
        self.data, self.calls = data, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=2000, output_tokens=500, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.data))])


def test_campaign_plan_fits_budget_and_asks_owner(ads, monkeypatch):
    layer = {"purpose": "p", "audience": "a", "creatives": "c", "success_metric": "s", "stop_rule": "x"}
    data = {"headline": "Add hot retargeting", "layers": [
        {**layer, "layer": "prospecting", "daily_budget": 800}, {**layer, "layer": "retarget_hot", "daily_budget": 200}],
        "sequence": [{"when": "day 0", "message": "hook", "format": "reel"}],
        "cross_channel": [{"channel": "whatsapp", "idea": "cart reminder", "when": "later"}],
        "proposals": [
            {"action": "retarget", "target_id": "AD1", "target_name": "Cart retargeting", "audience": "cart_14d",
             "daily_budget": 100, "reason": "441 people left at checkout", "expected": "ROAS 10+"},
            {"action": "pause", "target_id": "NOPE", "target_name": "Ghost", "audience": "broad", "daily_budget": 0,
             "reason": "r", "expected": "e"}]}
    asked = []
    monkeypatch.setattr(telegram, "send_buttons", lambda b, text, buttons=None: asked.append((text, buttons)))
    result = campaigns.plan(ads, client=PlanClaude(data))
    assert [round(x["daily_budget"]) for x in result["data"]["layers"]] == [400, 100]  # scaled to ₹500 total
    assert len(result["proposals"]) == 1 and "Ghost: unknown" in result["refused"][0]
    p = proposals.get(ads, result["proposals"][0])
    assert p["status"] == "proposed" and p["params"]["daily_budget"] == 100 and "added to cart" in p["summary"]
    assert any(b and b[0][1] == f"ad:y:{p['id']}" for _, b in asked)


def test_no_ad_account_no_campaign_plan(brand):
    assert campaigns.plan(load_brand("skipperpacks"), client=None) is None
