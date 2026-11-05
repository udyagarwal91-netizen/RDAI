"""Phases D + E checks: reading Meta ads and Shopify sales, Finance numbers and alerts, SMO findings,
and ad changes that only happen after the owner's ✅ (created paused, inside the limits). No network."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest

from app import goals, meta_ads, publisher, sales, shopify, telegram
from app.brands import load_brand
from app.db import get_state, set_state
from app.team import ad_actions, finance, proposals, smo
from test_control_center import _token, client  # noqa: F401 — shared fixture
from test_phase1 import brand  # noqa: F401 — shared fixture

TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()  # the agent counts days in India time


def _day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def _insight(day, ad, adset, spend, buys, value, clicks=100, imp=2000, freq=1.2):
    return {"date_start": day, "ad_id": ad, "ad_name": f"Ad {ad}", "adset_id": adset, "adset_name": f"Set {adset}",
            "campaign_id": "C1", "campaign_name": "Sales", "spend": str(spend), "impressions": str(imp),
            "reach": str(int(imp / freq)), "frequency": str(freq), "inline_link_clicks": str(clicks),
            "actions": [{"action_type": "omni_purchase", "value": str(buys)},
                        {"action_type": "purchase", "value": str(buys + 5)},  # same event, another name: ignored
                        {"action_type": "omni_add_to_cart", "value": "9"}],
            "action_values": [{"action_type": "omni_purchase", "value": str(value)}]}


class FakeGraph:
    """Stands in for graph.facebook.com: ad account reads and writes."""

    def __init__(self, currency="INR"):
        self.posts, self.currency = [], currency
        self.insights = []
        for n in range(1, 8):  # good ad AD1 in AS1; bad ad AD2 in AS2
            self.insights.append(_insight(_day(n), "AD1", "AS1", 400, 7, 2400))
            self.insights.append(_insight(_day(n), "AD2", "AS2", 60, 0, 0, clicks=10, freq=3.5))

    def call(self, method, url, token, files=None, **params):
        path = url.split("/v26.0/")[1]
        if method == "POST":
            self.posts.append((path, params))
            return {"id": f"new{len(self.posts)}"}
        if path == "me/adaccounts":
            return {"data": [{"account_id": "1840946140217408", "name": "Skipper Packs", "account_status": 1,
                              "currency": "INR"},
                             {"account_id": "644872447824093", "name": "Skippers", "account_status": 1,
                              "currency": "INR"}]}
        if path.startswith("act_") and "/" not in path:
            return {"currency": self.currency, "name": "Skipper Brands"}
        if path.endswith("/insights"):
            return {"data": self.insights, "paging": {"cursors": {"after": "x"}}}
        if path.endswith("/adsets"):
            return {"data": [{"id": "AS1", "name": "Set AS1", "effective_status": "ACTIVE", "daily_budget": "40000",
                              "campaign_id": "C1", "campaign": {"name": "Sales"}},
                             {"id": "AS2", "name": "Set AS2", "effective_status": "ACTIVE", "daily_budget": "6000",
                              "campaign_id": "C1", "campaign": {"name": "Sales"}}]}
        if path.endswith("/ads"):
            return {"data": [{"id": "AD1", "name": "Ad AD1", "effective_status": "ACTIVE", "adset_id": "AS1",
                              "creative": {"id": "CR1"}},
                             {"id": "AD2", "name": "Ad AD2", "effective_status": "ACTIVE", "adset_id": "AS2",
                              "creative": {"id": "CR2"}}]}
        if path.endswith("/adspixels"):
            return {"data": [{"id": "PX1", "name": "Pixel"}]}
        raise AssertionError(path)


def _orders():
    nodes = []
    for n in range(1, 8):
        for i in range(10):
            nodes.append({"createdAt": f"{_day(n)}T06:00:00Z", "cancelledAt": None, "test": False,
                          "paymentGatewayNames": ["Cash on Delivery (COD)"] if i < 4 else ["Razorpay"],
                          "totalPriceSet": {"shopMoney": {"amount": "300.00"}},
                          "totalRefundedSet": {"shopMoney": {"amount": "30.00" if i == 0 else "0"}},
                          "customer": {"numberOfOrders": "2" if i == 1 else "1"}})
    return nodes


@pytest.fixture
def shop(brand, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAtest123456789")
    graph = FakeGraph()
    monkeypatch.setattr(publisher, "_call", graph.call)

    def fake_graphql(b, query, variables=None):
        if "shopifyqlQuery" in query:
            return {"shopifyqlQuery": {"parseErrors": [], "tableData": {
                "columns": [{"name": "sessions"}, {"name": "sessions_with_cart_additions"},
                            {"name": "sessions_that_reached_checkout"}, {"name": "sessions_that_completed_checkout"}],
                "rows": [{"sessions": "5250", "sessions_with_cart_additions": "864",
                          "sessions_that_reached_checkout": "627", "sessions_that_completed_checkout": "186"}]}}}
        return {"orders": {"nodes": _orders(), "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    monkeypatch.setattr(shopify, "graphql", fake_graphql)
    said = []
    monkeypatch.setattr(telegram, "send_buttons", lambda b, text, buttons=None: said.append((text, buttons)))
    monkeypatch.setattr(telegram, "send_message", lambda b, text: said.append((text, None)))
    return SimpleNamespace(brand=load_brand("skipperpacks"), graph=graph, said=said)


def test_only_the_brands_own_ad_account_is_picked(shop):
    meta_ads.discover(shop.brand)
    assert meta_ads.account_id(shop.brand) == "act_1840946140217408"  # "Skippers" (agency account) ignored


def test_ads_and_sales_numbers(shop):
    meta_ads.discover(shop.brand)
    meta_ads.refresh(shop.brand)
    t = meta_ads.totals(shop.brand, 7)
    assert t["spend"] == 7 * 460 and t["purchases"] == 49 and t["roas"] == round(7 * 2400 / (7 * 460), 2)
    assert meta_ads.active_daily_budget(shop.brand) == 460  # paise → rupees
    sales.refresh(shop.brand)
    s = sales.totals(shop.brand, 7)
    assert s["orders"] == 70 and s["aov"] == 300 and s["cod_share"] == 40 and s["repeat_rate"] == 10
    assert sales.funnel(shop.brand)["checkout_completion"] == 29.7


def test_finance_measures_goals_break_even_and_alerts(shop):
    meta_ads.discover(shop.brand)
    doc = goals.load(shop.brand)
    doc["business"].update(product_cost_pct=40, shipping_cost_per_order=60, payment_fee_pct=2, cod_share_pct=40,
                           rto_pct=20)
    goals.save(shop.brand, doc)
    m = finance.daily_tick(shop.brand)
    assert m["metrics"]["blended_roas"] == round(21000 / 3220, 2) and m["metrics"]["checkout_completion"] == 29.7
    assert goals.measured(shop.brand)["aov"]["value"] == 300
    # 1 ÷ (1 − 0.40 − 0.02 − 60/300 − 0.08 × 1.2) = 3.52
    assert m["break_even_roas"] == 3.52
    assert not m["problems"]
    # Meta counts 49 sales, Shopify 70 orders → within 40%: no tracking alert; spend ₹460 < ₹500 limit → none
    assert not [a for a in m["alerts"] if "Tracking" in a or "limit" in a]


def test_smo_proposes_pausing_the_loser_once(shop):
    meta_ads.discover(shop.brand)
    meta_ads.refresh(shop.brand)
    f = smo.findings(shop.brand)
    assert {x["id"] for x in f["losers"]} >= {"AS2", "AD2"} and "AS1" in {x["id"] for x in f["winners"]}
    first = smo.daily_tick(shop.brand)
    assert first and smo.daily_tick(shop.brand) == []  # not asked twice
    texts = [t for t, b in shop.said if b and b[0][1].startswith("ad:y:")]
    assert texts and "Pause" in texts[0]


def test_approved_pause_and_budget_change(shop):
    meta_ads.discover(shop.brand)
    meta_ads.refresh(shop.brand)
    pid, _ = proposals.create(shop.brand, "smo", "pause", "AD2", "Ad AD2", {}, "no sales", "save money")
    assert ad_actions.decide(shop.brand, pid, True).startswith("✅ Paused")
    assert shop.graph.posts[-1] == ("AD2", {"status": "PAUSED"})
    assert "Already done" in ad_actions.decide(shop.brand, pid, True)
    # raising a budget: only with good blended ROAS, max +20%, inside the ₹500/day limit
    goals.record(shop.brand, {"blended_roas": 9}, "test")
    pid, _ = proposals.create(shop.brand, "smo", "budget", "AS1", "Set AS1", {"from": 400, "to": 600}, "winner", "more")
    assert proposals.get(shop.brand, pid)["params"]["to"] == 440  # limit: ₹500 − ₹60 (other ad set) = ₹440
    ad_actions.decide(shop.brand, pid, True)
    assert shop.graph.posts[-1][1]["daily_budget"] == "44000"


def test_unknown_object_is_never_touched(shop):
    meta_ads.discover(shop.brand)
    meta_ads.refresh(shop.brand)
    pid, _ = proposals.create(shop.brand, "smo", "pause", "OTHER_ACCOUNT_AD", "x", {}, "r", "e")
    assert "isn't in the brand's ad account" in ad_actions.decide(shop.brand, pid, True)
    assert not shop.graph.posts


def test_retargeting_is_created_paused_and_needs_a_second_ok(shop):
    meta_ads.discover(shop.brand)
    meta_ads.refresh(shop.brand)
    _, msg = proposals.create(shop.brand, "planner", "retarget", "AD1", "Cart retargeting",
                              {"audience": "cart_14d", "daily_budget": 200}, "r", "e")
    assert "No room" in msg  # ₹460 of the ₹500/day limit already runs
    doc = goals.load(shop.brand)
    doc["guardrails"]["daily_ad_budget"] = 600
    goals.save(shop.brand, doc)
    pid, msg = proposals.create(shop.brand, "planner", "retarget", "AD1", "Cart retargeting",
                                {"audience": "cart_14d", "daily_budget": 200}, "441 left at checkout", "ROAS 10+")
    assert proposals.get(shop.brand, pid)["params"]["daily_budget"] == 140  # shrunk to fit ₹600
    result = ad_actions.decide(shop.brand, pid, True)
    assert "PAUSED" in result
    paths = [p for p, _ in shop.graph.posts]
    assert paths[0].endswith("/customaudiences") and paths[1].endswith("/campaigns")
    assert all(params.get("status") == "PAUSED" for p, params in shop.graph.posts if not p.endswith("customaudiences"))
    rule = json.loads(shop.graph.posts[0][1]["rule"])
    assert rule["inclusions"]["rules"][0]["filter"]["filters"][0]["value"] == "AddToCart"
    assert rule["exclusions"]["rules"][0]["filter"]["filters"][0]["value"] == "Purchase"
    follow = proposals.listing(shop.brand, "proposed")
    assert follow and follow[0]["action"] == "activate"  # second ✅ to switch it on


def test_saying_no_and_tracking_freeze(shop):
    meta_ads.discover(shop.brand)
    meta_ads.refresh(shop.brand)
    pid, _ = proposals.create(shop.brand, "smo", "pause", "AD1", "Ad AD1", {}, "r", "e")
    assert ad_actions.decide(shop.brand, pid, False) == "OK — not doing it."
    assert proposals.get(shop.brand, pid)["status"] == "rejected" and not shop.graph.posts
    goals.record(shop.brand, {"blended_roas": 9}, "test")
    set_state("skipperpacks", "finance:tracking_suspect", "1")
    pid, _ = proposals.create(shop.brand, "smo", "budget", "AS2", "Set AS2", {"from": 60, "to": 70}, "r", "e")
    assert "frozen" in ad_actions.decide(shop.brand, pid, True)


def test_telegram_ad_button_starts_the_change(shop, monkeypatch):
    from app import jobs
    started = []
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: started.append((action, kw)) or True)
    monkeypatch.setattr(telegram, "_call", lambda *a, **k: {})
    telegram.Bot(lambda: shop.brand).handle_button(shop.brand, {"id": "q", "from": {"id": 12345},
                                                                 "data": "ad:y:7"}, "12345")
    assert started == [("ad_action", {"pid": 7, "yes": True})]


def test_money_and_ads_pages(client, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAtest123456789")
    monkeypatch.setattr(publisher, "_call", graph.call)
    b = load_brand("skipperpacks")
    meta_ads.discover(b)
    meta_ads.refresh(b)
    pid, _ = proposals.create(b, "smo", "pause", "AD2", "Ad AD2", {}, "no sales", "save")
    page = client.get("/ads").get_data(as_text=True)
    assert "Waiting for your OK" in page and "Set AS1" in page and "act_1840946140217408" in page
    from app import jobs
    started = []
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: started.append((action, kw)) or True)
    client.post(f"/ads/proposal/{pid}/yes", data={"csrf": _token(client)})
    assert started == [("ad_action", {"pid": pid, "yes": True})]
    assert "Refresh numbers now" in client.get("/money").get_data(as_text=True)
    assert get_state("skipperpacks", "meta:accounts")


def test_money_page_with_numbers(client, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAtest123456789")
    monkeypatch.setattr(publisher, "_call", graph.call)
    monkeypatch.setattr(telegram, "send_buttons", lambda *a, **k: None)
    monkeypatch.setattr(shopify, "graphql", lambda b, q, v=None: {
        "orders": {"nodes": _orders(), "pageInfo": {"hasNextPage": False}}} if "orders" in q else {})
    b = load_brand("skipperpacks")
    meta_ads.discover(b)
    finance.daily_tick(b)
    page = client.get("/money").get_data(as_text=True)
    assert "Blended ROAS" in page and "₹3,220" in page and "Break-even ROAS: not known yet" in page
    assert "read_reports" in page  # the missing permission is explained, not hidden



def test_chosen_ad_account_in_dollars_is_converted_to_rupees(shop):
    shop.graph.currency = "USD"
    meta_ads.set_account(shop.brand, "act_1671153230648383")
    assert meta_ads.account_id(shop.brand) == "act_1671153230648383" and meta_ads.currency(shop.brand) == "USD"
    meta_ads.discover(shop.brand)  # a chosen account is never replaced by the name match
    assert meta_ads.account_id(shop.brand) == "act_1671153230648383"
    meta_ads.refresh(shop.brand)
    t = meta_ads.totals(shop.brand, 7)
    assert t["spend"] == 7 * 460 * 88 and meta_ads.active_daily_budget(shop.brand) == 460 * 88
    goals.record(shop.brand, {"blended_roas": 9}, "test")
    doc = goals.load(shop.brand)
    doc["guardrails"].update(daily_ad_budget=50000, monthly_ad_budget=10_000_000)
    goals.save(shop.brand, doc)
    pid, _ = proposals.create(shop.brand, "smo", "budget", "AS1", "Set AS1", {"from": 35200, "to": 38720}, "w", "m")
    ad_actions.decide(shop.brand, pid, True)
    assert shop.graph.posts[-1][1]["daily_budget"] == "44000"  # ₹38,720 = US$440.00 = 44000 cents


def test_ads_page_takes_a_typed_account_id(client, monkeypatch):
    graph = FakeGraph("USD")
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAtest123456789")
    monkeypatch.setattr(publisher, "_call", graph.call)
    client.post("/ads", data={"csrf": _token(client), "account": "1671153230648383"})
    page = client.get("/ads").get_data(as_text=True)
    assert "Skipper Brands" in page and "act_1671153230648383" in page and "in USD" in page
