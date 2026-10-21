"""The weekly content plan.

make_plan() looks at the products (from Shopify), the brand rules, the
posting slots, upcoming festivals and past results, asks Claude for a plan,
checks it, and saves it. Claude only chooses *what* to post and *how*; every
price shown with the plan comes from Shopify, and anything in Claude's ideas
that doesn't match Shopify is flagged for you.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import calendar_india, claude, factcheck
from app.brands import Brand
from app.db import connect

log = logging.getLogger(__name__)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
THEMES = ["product", "offer", "education", "lifestyle"]
FORMATS = ["template_image", "carousel", "gemini_image", "gemini_reel"]
FORMAT_LABELS = {"template_image": "🖼 Brand image", "carousel": "🗂 Carousel",
                 "gemini_image": "✨ AI image", "gemini_reel": "🎬 Reel"}


class PlanError(Exception):
    """A problem making the plan, with a message the owner can act on."""


@dataclass
class Slot:
    index: int
    when: datetime   # in the brand's timezone

    def label(self) -> str:
        return self.when.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


# ---------------------------------------------------------------- inputs

def catalog_styles(brand: Brand) -> list[dict]:
    """Products that can be posted (active, in stock), grouped by style.

    Shopify has one product per colour with the same title, so a "style" is
    all products sharing a title. Each style gets a short id (S1, S2…) that
    Claude uses to pick it.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE brand = ? AND status = 'ACTIVE' AND COALESCE(total_stock, 0) > 0",
            (brand.slug,)).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r["title"].strip().lower()].append(r)

    styles = []
    for i, (_, items) in enumerate(sorted(groups.items(), key=lambda kv: kv[0]), start=1):
        if not any(v["price"] > 0 for r in items for v in json.loads(r["variants"])):
            continue  # no real price at all — never post it
        variants = [v for r in items for v in json.loads(r["variants"]) if v["price"] > 0]
        prices = sorted({v["price"] for v in variants})
        mrps = sorted({v["compare_at"] for v in variants if v.get("compare_at") and v["compare_at"] > v["price"]})
        sizes = []
        for v in variants:
            if v["size"] not in sizes:
                sizes.append(v["size"])
        newest = max(r["shopify_created"] or "" for r in items)
        styles.append({
            "id": f"S{i}",
            "title": items[0]["title"],
            "pack_size": items[0]["pack_size"],
            "price_from": prices[0],
            "price_to": prices[-1],
            "all_prices": prices,
            "mrp_from": mrps[0] if mrps else None,
            "all_mrps": mrps,
            "sizes": sizes,
            "colourways": len(items),
            "stock": sum(r["total_stock"] or 0 for r in items),
            "newest_added": newest[:10],
            "product_type": items[0]["product_type"],
            "description": (items[0]["description"] or "")[:400],
            "product_ids": [r["shopify_id"] for r in sorted(items, key=lambda r: -(r["total_stock"] or 0))],
            "image": json.loads(items[0]["images"])[0] if json.loads(items[0]["images"]) else None,
            "issues": [f"{r['handle']}: {i}" for r in items for i in json.loads(r["issues"] or "[]")],
        })
    return styles


def plan_slots(brand: Brand, start: date) -> list[Slot]:
    """Every posting slot from `start` for the plan length (7 or 14 days)."""
    sched = brand.config["schedule"]
    tz = ZoneInfo(sched.get("timezone", "Asia/Kolkata"))
    days = int(sched.get("plan_horizon_days", 7))
    slots = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for s in sched.get("slots", []):
            if s["day"] == DAYS[day.weekday()]:
                hh, mm = map(int, str(s["time"]).split(":"))
                slots.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz))
    return [Slot(i, w) for i, w in enumerate(sorted(slots), start=1)]


def _split(total: int, shares: dict[str, int]) -> dict[str, int]:
    """Turn percentages into whole post counts that add up to `total`."""
    raw = {k: total * v / 100 for k, v in shares.items()}
    counts = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[: total - sum(counts.values())]:
        counts[k] += 1
    return counts


def targets(brand: Brand, n_slots: int) -> tuple[dict, dict, int]:
    """How many posts of each topic and format to aim for, and the reel cap."""
    mix = brand.config["content_mix"]
    days = int(brand.config["schedule"].get("plan_horizon_days", 7))
    reel_cap = int(mix.get("max_reels_per_week", 0)) * days // 7
    themes = _split(n_slots, mix["themes"])
    formats = _split(n_slots, mix["formats"])
    if formats["gemini_reel"] > reel_cap:
        formats["gemini_image"] += formats["gemini_reel"] - reel_cap
        formats["gemini_reel"] = reel_cap
    return themes, formats, reel_cap


def performance_summary(brand: Brand) -> str:
    """What worked in the last 4 weeks, in a few lines (empty until Phase 4 collects results)."""
    with connect() as conn:
        rows = conn.execute("""
            SELECT d.format, cp.theme, AVG(i.reach) AS reach, AVG(i.saves) AS saves, AVG(i.shares) AS shares,
                   COUNT(*) AS n
            FROM insights i JOIN posts p ON p.id = i.post_id JOIN drafts d ON d.id = p.draft_id
            LEFT JOIN content_plan cp ON cp.id = d.plan_id
            WHERE i.brand = ? AND i.collected_at >= date('now', '-28 days')
            GROUP BY d.format, cp.theme ORDER BY saves DESC""", (brand.slug,)).fetchall()
    if not rows:
        return "No post results yet — this is one of the first plans. Use a balanced, varied mix."
    lines = [f"- {r['format']} / {r['theme']}: {r['n']} posts, avg reach {r['reach']:.0f}, "
             f"saves {r['saves']:.1f}, shares {r['shares']:.1f}" for r in rows]
    return "Last 4 weeks (best first by saves):\n" + "\n".join(lines)


def recent_ideas(brand: Brand) -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT idea FROM content_plan WHERE brand = ? AND scheduled_for >= date('now', '-21 days') "
                            "ORDER BY scheduled_for DESC LIMIT 20", (brand.slug,)).fetchall()
    return [r["idea"] for r in rows]


# ---------------------------------------------------------------- prompt

def system_prompt(brand: Brand) -> str:
    c = brand.config
    voice = c["voice"]
    return f"""You are the social media content planner for {brand.name}, posting to Instagram and Facebook.

Your job: decide what goes in each posting slot — topic, format, which product style, a one-line idea and an
opening hook. Another step writes the final caption and makes the visuals later, so keep ideas short and concrete.

Non-negotiable rules:
1. Facts come only from the product data you are given. Never invent prices, discounts, offers, "limited stock",
   free gifts, delivery promises, sizes, colours or pack sizes. If you mention a price, use exactly a price listed for
   that style (say "from ₹X" when prices vary by size). An "offer" post may only use a real crossed-out MRP vs price
   from the data.
2. Innerwear content must never be suggestive or sexualised: no bedroom scenes, no lingering body shots, no innuendo,
   no body-shaming. Premium everyday-essentials tone — something a customer's parents could see in their feed.
   Prefer product-only visuals (flat-lays, folded packs, gym bag, travel kit, getting-ready routines).
3. No competitor names, no health or medical claims.
4. Never use these words: {", ".join(c.get("banned_words") or [])}.
5. For festivals, only use the dates you are given. Dates marked approx: build up to them, don't promise a same-day
   delivery or event.

Voice: {voice.get("tone", "").strip()}
Language: {voice.get("language")} — about {voice.get("hinglish_share_percent", 0)}% of hooks may be light Hinglish;
product facts always in clear English. Emojis: {voice.get("emoji_level")}.

Formats you can choose:
- template_image: clean brand-template graphic with the real product photo, name and price. Best for product/offer.
- carousel: 3–5 slides (e.g. "what's in the pack", size guide, care tips). Best for education.
- gemini_image: AI lifestyle or flat-lay image built from the real product photo. Best for lifestyle.
- gemini_reel: short vertical AI video from the product photo. Most engaging but expensive — use only where it adds a lot.

Plan well: vary styles across the week (don't repeat a style on consecutive slots unless there are very few styles),
favour newly added and well-stocked styles, build up to upcoming festivals, and avoid repeating recent ideas.

The brand's own rules, written by the owner (follow them):
<brand_rules>
{brand.rules.strip()}
</brand_rules>"""


def user_prompt(brand: Brand, styles: list[dict], slots: list[Slot], themes: dict, formats: dict, reel_cap: int,
                festivals: list[dict], performance: str, recent: list[str]) -> str:
    style_lines = []
    for s in styles:
        price = f"₹{s['price_from']:g}" if s["price_from"] == s["price_to"] else f"₹{s['price_from']:g}–₹{s['price_to']:g} by size"
        mrp = f"; MRP from ₹{s['mrp_from']:g}" if s["mrp_from"] else ""
        style_lines.append(
            f"{s['id']}: {s['title']} | {s['pack_size'] or '?'}-pc pack | {price}{mrp} | sizes {', '.join(s['sizes'])} | "
            f"{s['colourways']} colourway(s) | stock {s['stock']} | added {s['newest_added']} | {s['product_type']}")
    slot_lines = [f"{s.index}: {s.label()}" for s in slots]
    fest = "\n".join(f"- {f['date']}: {f['name']} ({f['note']})" for f in festivals) or "- none in this period"
    recent_text = "\n".join(f"- {i}" for i in recent) or "- none"
    return f"""Plan these {len(slots)} posting slots (India time):
{chr(10).join(slot_lines)}

Aim for this mix (±1 is fine): topics {json.dumps(themes)}; formats {json.dumps(formats)}.
Hard limit: at most {reel_cap} gemini_reel posts.

Product styles you can use (in stock right now):
{chr(10).join(style_lines)}

Festivals and sale days:
{fest}

How past posts performed:
{performance}

Recent ideas to avoid repeating:
{recent_text}

Return one entry per slot. "idea" is one sentence describing the post. "hook" is the first line people will read.
"why" is a few words on why this fits the plan. "styles" lists the style ids shown (1–3; usually 1)."""


def plan_schema(styles: list[dict], slots: list[Slot]) -> dict:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slot": {"type": "integer", "enum": [s.index for s in slots]},
                        "theme": {"type": "string", "enum": THEMES},
                        "format": {"type": "string", "enum": FORMATS},
                        "styles": {"type": "array", "items": {"type": "string", "enum": [s["id"] for s in styles]}},
                        "idea": {"type": "string"},
                        "hook": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["slot", "theme", "format", "styles", "idea", "hook", "why"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "posts"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------- checks

def check_post(post: dict, styles_by_id: dict, brand: Brand) -> list[str]:
    """Problems with one planned post, in plain English (empty list = fine)."""
    allowed, packs = set(), set()
    for sid in post["styles"]:
        s = styles_by_id[sid]
        allowed |= set(s["all_prices"]) | set(s["all_mrps"])
        if s["pack_size"]:
            packs.add(s["pack_size"])
    return factcheck.check_text(f"{post['idea']} {post['hook']}", brand, allowed, packs)


# ---------------------------------------------------------------- main

def make_plan(brand: Brand, start: date | None = None, client=None) -> dict:
    """Make and save the plan. Returns {"summary", "rows", "cost_usd", "model"}."""
    tz = ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata"))
    start = start or (datetime.now(tz).date() + timedelta(days=1))
    styles = catalog_styles(brand)
    if not styles:
        raise PlanError("No products to plan with yet. Press “Get products from Shopify” first "
                        "(or check that products are active and in stock).")
    slots = plan_slots(brand, start)
    if not slots:
        raise PlanError("No posting days are ticked. Choose them on Brand → Posting days.")
    themes, formats, reel_cap = targets(brand, len(slots))
    end = slots[-1].when.date()
    festivals = calendar_india.upcoming(start, end, (brand.config.get("festivals") or {}).get("custom_dates"))

    try:
        client = client or claude.client_for(brand)
    except claude.ClaudeError as e:
        raise PlanError(str(e)) from None

    messages = [{"role": "user", "content": user_prompt(
        brand, styles, slots, themes, formats, reel_cap, festivals, performance_summary(brand), recent_ideas(brand))}]
    styles_by_id = {s["id"]: s for s in styles}
    cost, model_used, plan = 0.0, claude.MODEL, None
    for attempt in range(2):
        try:
            plan, call_cost, model_used = claude.ask(client, system_prompt(brand), messages, plan_schema(styles, slots))
        except claude.ClaudeError as e:
            raise PlanError(str(e)) from None
        cost += call_cost
        problem = _structure_problem(plan, slots)
        if not problem:
            break
        log.warning("Plan attempt %d had a problem (%s); asking again", attempt + 1, problem)
        messages += [{"role": "assistant", "content": json.dumps(plan)},
                     {"role": "user", "content": f"That plan has a problem: {problem}. Return the full corrected plan."}]
    else:
        raise PlanError(f"Claude's plan didn't fit the posting slots ({problem}). Press the button again.")

    rows = _save(brand, plan, slots, styles_by_id, start, reel_cap)
    log.info("Plan for %s: %d posts from %s, model %s, cost about US$%.3f", brand.slug, len(rows), start,
             model_used, cost)
    return {"summary": plan["summary"], "rows": rows, "cost_usd": cost, "model": model_used}


def _structure_problem(plan: dict, slots: list[Slot]) -> str | None:
    used = [p["slot"] for p in plan.get("posts", [])]
    missing = sorted({s.index for s in slots} - set(used))
    dupes = sorted({u for u in used if used.count(u) > 1})
    if missing:
        return f"slots {missing} have no post"
    if dupes:
        return f"slots {dupes} have more than one post"
    if any(not p["styles"] for p in plan["posts"]):
        return "a post has no product style"
    return None


def _save(brand: Brand, plan: dict, slots: list[Slot], styles_by_id: dict, start: date, reel_cap: int) -> list[dict]:
    by_slot = {s.index: s for s in slots}
    posts = sorted(plan["posts"], key=lambda p: p["slot"])
    reels = 0
    now = datetime.now(by_slot[posts[0]["slot"]].when.tzinfo).isoformat(timespec="seconds")
    rows = []
    with connect() as conn:
        # A new plan replaces anything still only "planned" in the same period.
        conn.execute("DELETE FROM content_plan WHERE brand = ? AND status = 'planned' AND scheduled_for >= ?",
                     (brand.slug, slots[0].when.isoformat()))
        for p in posts:
            notes = check_post(p, styles_by_id, brand)
            fmt = p["format"]
            if fmt == "gemini_reel":
                reels += 1
                if reels > reel_cap:
                    fmt = "gemini_image"
                    notes.append("changed from reel to AI image (weekly reel limit)")
            product_ids = [pid for sid in p["styles"] for pid in styles_by_id[sid]["product_ids"][:1]]
            row = {
                "brand": brand.slug, "week_start": start.isoformat(),
                "scheduled_for": by_slot[p["slot"]].when.isoformat(), "theme": p["theme"], "format": fmt,
                "product_ids": json.dumps(product_ids), "idea": p["idea"].strip(), "hook": p["hook"].strip(),
                "notes": json.dumps({"why": p["why"].strip(), "warnings": notes,
                                     "styles": [styles_by_id[s]["title"] for s in p["styles"]]}),
                "status": "planned", "created_at": now,
            }
            cur = conn.execute(f"INSERT INTO content_plan ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                               list(row.values()))
            rows.append({**row, "id": cur.lastrowid})
    return rows


# ---------------------------------------------------------------- reading the plan back

def upcoming_plan(brand: Brand) -> list[dict]:
    """Planned posts from today on, with product details from Shopify data for display."""
    tz = ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata"))
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM content_plan WHERE brand = ? AND scheduled_for >= ? ORDER BY scheduled_for",
                            (brand.slug, today)).fetchall()
        products = {r["shopify_id"]: r for r in conn.execute("SELECT * FROM products WHERE brand = ?", (brand.slug,))}
    out = []
    for r in rows:
        notes = json.loads(r["notes"] or "{}")
        prods = []
        for pid in json.loads(r["product_ids"] or "[]"):
            p = products.get(pid)
            if p:
                images = json.loads(p["images"] or "[]")
                prods.append({"title": p["title"], "pack_size": p["pack_size"], "min_price": p["min_price"],
                              "max_price": p["max_price"], "mrp": p["compare_at_min"],
                              "image": images[0] if images else None, "url": p["url"]})
        out.append({**dict(r), "when": datetime.fromisoformat(r["scheduled_for"]), "products": prods,
                    "why": notes.get("why", ""), "warnings": notes.get("warnings", []),
                    "format_label": FORMAT_LABELS.get(r["format"], r["format"])})
    return out


def price_text(p: dict) -> str:
    """'₹249' or 'from ₹249' — straight from Shopify."""
    if p["max_price"] and p["max_price"] > p["min_price"]:
        return f"from ₹{p['min_price']:g}"
    return f"₹{p['min_price']:g}"
