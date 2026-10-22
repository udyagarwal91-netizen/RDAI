"""Phase 1 checks: Shopify sync, grouping, plan targets, price checks, planning, Telegram."""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app import planner, secrets_store, shopify, telegram
from app.brands import load_brand
from app.db import connect


def _node(pid, handle, title, prices, mrps, stock=100, tags=("3 Pcs Pack",), created="2026-08-01T13:22:19Z"):
    """A product shaped exactly like Shopify's answer (copied from the real store's response)."""
    return {
        "id": f"gid://shopify/Product/{pid}", "handle": handle, "title": title,
        "description": "Pack of 3 men's briefs", "productType": "Men's Innerwear", "tags": list(tags),
        "status": "ACTIVE", "createdAt": created, "totalInventory": stock,
        "onlineStoreUrl": f"https://skipperpacks.com/products/{handle}",
        "priceRangeV2": {"minVariantPrice": {"amount": str(min(prices)), "currencyCode": "INR"},
                         "maxVariantPrice": {"amount": str(max(prices))}},
        "collections": {"nodes": [{"title": "Colin Active Brief"}]},
        "featuredMedia": {"image": {"url": f"https://cdn.shopify.com/{handle}.jpg?v=1"}},
        "media": {"nodes": [{"image": {"url": f"https://cdn.shopify.com/{handle}.jpg?v=1"}}, {}]},
        "variants": {"nodes": [
            {"title": f"{80 + 5 * i}cm", "sku": f"SKU{i}", "price": f"{p:.2f}", "compareAtPrice": f"{m:.2f}",
             "inventoryQuantity": stock // len(prices), "availableForSale": True,
             "selectedOptions": [{"name": "Size", "value": f"{80 + 5 * i}cm"}]}
            for i, (p, m) in enumerate(zip(prices, mrps))]},
    }


CATALOG = [
    _node(1, "colin-active-brief-3-pcs-pack-7", "Colin Active Brief 3 Pcs Pack", [249, 249, 279], [339, 339, 379]),
    _node(2, "colin-active-brief-3-pcs-pack-6", "Colin Active Brief 3 Pcs Pack", [249, 259], [339, 369]),
    _node(3, "colin-classic-trunk-2-pcs", "Colin Classic Trunk 2 Pcs Pack", [199], [249], tags=("2 Pcs Pack",)),
    _node(4, "colin-gym-vest-sold-out", "Colin Gym Vest 2pcs Pack", [299], [399], stock=0),
]


@pytest.fixture
def brand(sandbox, monkeypatch):
    from app import brand_editor

    brand_editor.update_config("skipperpacks", {"shopify.store_domain": "6arfzx-hb.myshopify.com"})
    monkeypatch.setenv("SHOPIFY_ADMIN_TOKEN", "shpat_test_token_123456")
    pages = [CATALOG[:2], CATALOG[2:]]

    def fake_graphql(b, query, variables=None):
        i = 0 if not variables.get("after") else 1
        return {"products": {"nodes": pages[i], "pageInfo": {"hasNextPage": i == 0, "endCursor": "c1"}}}

    monkeypatch.setattr(shopify, "graphql", fake_graphql)
    return load_brand("skipperpacks")


@pytest.mark.parametrize("title,tags,expected", [
    ("Colin Active Brief 3 Pcs Pack", [], 3),
    ("Colin gym vest 2pcs pack", [], 2),
    ("Classic Trunk Pack of 2", [], 2),
    ("Colin Gold Urocut", ["3 Pcs Pack"], 3),
    ("Colin Gold Urocut", ["Men Innerwear"], None),
])
def test_pack_size(title, tags, expected):
    assert shopify.pack_size(title, tags) == expected


def test_sync_stores_products_and_removes_deleted(brand):
    r = shopify.sync_products(brand)
    assert (r.total, r.new, r.in_stock) == (4, 4, 3)
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE handle = 'colin-active-brief-3-pcs-pack-7'").fetchone()
        assert row["pack_size"] == 3 and row["min_price"] == 249 and row["max_price"] == 279
        assert row["compare_at_min"] == 339
        conn.execute("INSERT INTO products (brand, shopify_id, title, synced_at) VALUES ('skipperpacks', 'old', 'Old', 'x')")
    assert shopify.sync_products(brand).removed == 1


def test_styles_group_colours_and_skip_out_of_stock(brand):
    shopify.sync_products(brand)
    styles = {s["title"]: s for s in planner.catalog_styles(brand)}
    assert set(styles) == {"Colin Active Brief 3 Pcs Pack", "Colin Classic Trunk 2 Pcs Pack"}
    brief = styles["Colin Active Brief 3 Pcs Pack"]
    assert brief["colourways"] == 2 and brief["price_from"] == 249 and brief["price_to"] == 279
    assert brief["mrp_from"] == 339 and brief["pack_size"] == 3


def test_targets_add_up_and_respect_reel_cap(brand):
    themes, formats, cap = planner.targets(brand, 5)
    assert sum(themes.values()) == 5 and sum(formats.values()) == 5
    assert formats["gemini_reel"] <= cap == 4


def test_slots_follow_schedule(brand):
    slots = planner.plan_slots(brand, date(2026, 10, 26))  # a Monday
    assert [s.label() for s in slots][:2] == ["Mon 26 Oct, 7:30 PM", "Wed 28 Oct, 7:30 PM"]
    assert len(slots) == 5


def test_price_check_catches_invented_prices(brand):
    shopify.sync_products(brand)
    styles = {s["id"]: s for s in planner.catalog_styles(brand)}
    sid = next(k for k, s in styles.items() if s["pack_size"] == 3)
    ok = {"styles": [sid], "idea": "Pack of 3 from ₹249, MRP ₹339", "hook": "Roz ka comfort"}
    bad = {"styles": [sid], "idea": "Now only ₹199! 40% off", "hook": "Sexy new pack, free gift"}
    assert planner.check_post(ok, styles, brand) == []
    problems = " ".join(planner.check_post(bad, styles, brand))
    assert "₹199" in problems and "%" in problems and "sexy" in problems and "free" in problems


class FakeClaude:
    """Stands in for Claude: returns a plan using the given slots and styles."""

    def __init__(self, make_plan):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._make_plan = make_plan

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        schema = kwargs["output_config"]["format"]["schema"]["properties"]["posts"]["items"]["properties"]
        plan = self._make_plan(schema["slot"]["enum"], schema["styles"]["items"]["enum"], len(self.calls))
        usage = SimpleNamespace(input_tokens=5000, output_tokens=2000, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(plan))])


def _plan(slot_ids, style_ids, call_no, fmt="gemini_reel"):
    posts = [{"slot": s, "theme": "product", "format": fmt, "styles": [style_ids[i % len(style_ids)]],
              "idea": "Show the 3-pc pack folded, from ₹249", "hook": "Roz ka comfort, sorted.", "why": "new"}
             for i, s in enumerate(slot_ids)]
    if call_no == 1:
        posts = posts[:-1]  # first answer misses a slot → planner should ask again
    return {"summary": "A balanced week.", "posts": posts}


def test_make_plan_saves_rows_retries_and_caps_reels(brand, monkeypatch):
    shopify.sync_products(brand)
    fake = FakeClaude(_plan)
    result = planner.make_plan(brand, start=date(2026, 10, 26), client=fake)
    assert len(fake.calls) == 2  # retried once because a slot was missing
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert "Never invent prices" in call["system"][0]["text"] and "50+ years" in call["system"][0]["text"]
    assert len(result["rows"]) == 5 and result["cost_usd"] > 0
    formats = [r["format"] for r in result["rows"]]
    assert formats.count("gemini_reel") == 4 and formats.count("gemini_image") == 1  # 5th reel over the cap


def test_new_plan_replaces_old_planned_rows(brand):
    shopify.sync_products(brand)
    planner.make_plan(brand, start=date(2026, 10, 26), client=FakeClaude(lambda a, b, c: _plan(a, b, 2)))
    planner.make_plan(brand, start=date(2026, 10, 26), client=FakeClaude(lambda a, b, c: _plan(a, b, 2)))
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_plan").fetchone()[0] == 5


def test_plan_without_products_explains_what_to_do(brand):
    with pytest.raises(planner.PlanError, match="Get products from Shopify"):
        planner.make_plan(brand, client=FakeClaude(_plan))


def test_telegram_message_uses_shopify_prices(brand, monkeypatch):
    shopify.sync_products(brand)
    planner.make_plan(brand, start=date(2099, 1, 5), client=FakeClaude(lambda a, b, c: _plan(a, b, 2, "carousel")))
    text = telegram.format_plan(brand, "A balanced week.", planner.upcoming_plan(brand), 0.05)
    assert "from ₹249" in text and "(3-pc)" in text and "🗂 Carousel" in text and "US$0.05" in text


def test_bot_only_answers_owner(brand, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda b, text: sent.append(text))
    bot = telegram.Bot(lambda: brand)
    bot.handle(brand, {"chat": {"id": 999}, "text": "/plan"}, "12345")
    assert sent == []
    bot.handle(brand, {"chat": {"id": 12345}, "text": "/help"}, "12345")
    bot.handle(brand, {"chat": {"id": 12345}, "text": "/brand"}, "12345")
    assert "Commands" in sent[0] and "later phase" in sent[1]


def test_long_messages_are_split():
    parts = telegram._split("\n\n".join(["x" * 1000] * 10))
    assert len(parts) >= 3 and all(len(p) <= telegram.MAX_LEN for p in parts)


def test_festival_form_and_calendar(brand):
    from app import brand_editor, calendar_india
    from app.web import brand_forms

    brand_editor.update_config("skipperpacks", brand_forms.festivals(
        {"custom_dates": "2026-11-20 | Winter Sale | real Shopify prices\n"}))
    custom = load_brand("skipperpacks").config["festivals"]["custom_dates"]
    items = calendar_india.upcoming(date(2026, 11, 1), date(2026, 11, 21), custom)
    names = [i["name"] for i in items]
    assert "Diwali" in names and "Winter Sale" in names
    with pytest.raises(Exception, match="Line 1"):
        brand_forms.festivals({"custom_dates": "20/11/2026 Winter Sale"})


def test_actions_run_in_background(brand, monkeypatch):
    from app import jobs

    monkeypatch.setitem(jobs.ACTIONS, "sync", lambda slug: "Got 4 products")
    assert jobs.start("skipperpacks", "sync")
    import time
    for _ in range(50):
        if jobs.status("skipperpacks")["sync"]["state"] != "running":
            break
        time.sleep(0.05)
    assert jobs.status("skipperpacks")["sync"] == {**jobs.status("skipperpacks")["sync"], "state": "done",
                                                   "message": "Got 4 products"}


def test_zero_price_is_never_used_and_is_reported(brand, monkeypatch):
    zero = _node(5, "colin-active-boxer-3-pcs-pack-7", "Colin Active Boxer 3 Pcs Pack", [299, 0, 0], [429, 0, 0])
    monkeypatch.setattr(shopify, "graphql", lambda b, q, v=None: {
        "products": {"nodes": [zero], "pageInfo": {"hasNextPage": False, "endCursor": None}}})
    r = shopify.sync_products(brand)
    assert r.problems == 1
    style = planner.catalog_styles(brand)[0]
    assert style["price_from"] == 299 and 0 not in style["all_prices"]
    assert "price ₹0" in style["issues"][0]
