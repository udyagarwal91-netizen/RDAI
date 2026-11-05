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
from app.db import connect, get_state, set_state

log = logging.getLogger(__name__)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
THEMES = ["product", "offer", "education", "lifestyle"]
FORMATS = ["template_image", "carousel", "gemini_image", "gemini_reel"]
FORMAT_LABELS = {"template_image": "🖼 Brand image", "carousel": "🗂 Carousel",
                 "gemini_image": "✨ AI image", "gemini_reel": "🎬 Reel", "story": "⭕ Story"}


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


def plan_slots(brand: Brand, start: date, days: int | None = None) -> list[Slot]:
    """Every feed posting slot from `start` for the plan length (default: the Brand page setting, 7 or 14 days)."""
    sched = brand.config["schedule"]
    tz = ZoneInfo(sched.get("timezone", "Asia/Kolkata"))
    days = days or int(sched.get("plan_horizon_days", 7))
    slots = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for s in sched.get("slots", []):
            if s["day"] == DAYS[day.weekday()]:
                hh, mm = map(int, str(s["time"]).split(":"))
                slots.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz))
    return [Slot(i, w) for i, w in enumerate(sorted(slots), start=1)]


STORY_TIMES = ["10:00", "20:00"]
STORY_TYPES = ["poll", "quiz", "question", "link", "festival", "product", "behind_the_scenes", "review"]
GOALS = ["awareness", "engagement", "consideration", "traffic", "sales", "trust", "education"]


def story_slots(brand: Brand, start: date, days: int) -> list[Slot]:
    """Possible Story times (any day; the Planner decides how many Stories to use)."""
    tz = ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata"))
    times = brand.config["schedule"].get("story_times") or STORY_TIMES
    out = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for t in times:
            hh, mm = map(int, str(t).split(":"))
            out.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz))
    return [Slot(1000 + i, w) for i, w in enumerate(out, start=1)]


def _split(total: int, shares: dict[str, int]) -> dict[str, int]:
    """Turn percentages into whole post counts that add up to `total`."""
    raw = {k: total * v / 100 for k, v in shares.items()}
    counts = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[: total - sum(counts.values())]:
        counts[k] += 1
    return counts


def targets(brand: Brand, n_slots: int, days: int | None = None) -> tuple[dict, dict, int]:
    """How many posts of each topic and format to aim for, and the reel cap."""
    mix = brand.config["content_mix"]
    days = days or int(brand.config["schedule"].get("plan_horizon_days", 7))
    reel_cap = int(mix.get("max_reels_per_week", 0)) * days // 7
    themes = _split(n_slots, mix["themes"])
    formats = _split(n_slots, mix["formats"])
    if formats["gemini_reel"] > reel_cap:
        formats["gemini_image"] += formats["gemini_reel"] - reel_cap
        formats["gemini_reel"] = reel_cap
    return themes, formats, reel_cap


def performance_summary(brand: Brand) -> str:
    """What worked in the last 4 weeks: numbers by format/topic/style/time plus the analyst's lessons."""
    from app import insights
    return insights.for_planner(brand)


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

Your skill playbook as the team's Content Planner (follow it; ad campaigns are planned in a separate step):
<playbook>
{_playbook(brand)}
</playbook>

The brand's own rules, written by the owner (follow them):
<brand_rules>
{brand.rules.strip()}
</brand_rules>"""


def _playbook(brand: Brand) -> str:
    from app import team
    return team.skill(brand, "planner").strip()


def user_prompt(brand: Brand, styles: list[dict], slots: list[Slot], themes: dict, formats: dict, reel_cap: int,
                festivals: list[dict], performance: str, recent: list[str], stories: list[Slot] | None = None,
                pillars: list[dict] | None = None, context: str = "") -> str:
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
"why" is a few words on why this fits the plan. "styles" lists the style ids shown (1–3; usually 1).
For each post also give: "pillar" (one of your content pillars), "goal", "tagline" (a short line for the visual,
max 6 words, no prices), "cta" (the call to action), "direction" (creative direction for the Creator: how the visual
or reel should look and flow, 1–2 sentences), and "boost" (true only for a post worth putting ad money behind, with
"boost_budget" in ₹ per day, else false and 0). Call products by their names from the product list.
{_story_part(stories)}
{_pillar_part(pillars)}{context}"""


def _story_part(stories: list[Slot] | None) -> str:
    if not stories:
        return 'Return "stories" as an empty list.'
    return ("STORIES: you may also plan Instagram/Facebook Stories at these times (use as many as are useful — "
            "typically 2–4 a week; polls, quizzes, questions, link stories, festival greetings, product teasers, "
            "behind the scenes, real reviews):\n" + "\n".join(f"{s.index}: {s.label()}" for s in stories) +
            '\nFor each story give "story_slot", "story_type", "styles" (may be empty), "idea", "hook" (the words on '
            'the story), "sticker" (e.g. "Poll: BRIEF / TRUNK", or empty), "pillar", "goal" and "why". Stickers are '
            "added by hand in the Instagram app, so keep the sticker text short. Never invent reviews.")


def _pillar_part(pillars: list[dict] | None) -> str:
    if pillars:
        return ("CONTENT PILLARS already set for this month (keep using them; return them unchanged in "
                f"\"pillars\"): {json.dumps(pillars, ensure_ascii=False)}\n")
    return ("CONTENT PILLARS: decide 4–6 content pillars for this brand right now (from the products, results, "
            "festivals and research) and return them in \"pillars\" with a one-line reason each. Balance the plan "
            "across them.\n")


def plan_schema(styles: list[dict], slots: list[Slot], stories: list[Slot] | None = None) -> dict:
    style_ids = [s["id"] for s in styles]
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "pillars": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "why": {"type": "string"}},
                "required": ["name", "why"], "additionalProperties": False}},
            "stories": {"type": "array", "items": {"type": "object", "properties": {
                "story_slot": {"type": "integer", "enum": [s.index for s in stories] or [0]},
                "story_type": {"type": "string", "enum": STORY_TYPES},
                "styles": {"type": "array", "items": {"type": "string", "enum": style_ids}},
                "idea": {"type": "string"}, "hook": {"type": "string"}, "sticker": {"type": "string"},
                "pillar": {"type": "string"}, "goal": {"type": "string", "enum": GOALS}, "why": {"type": "string"}},
                "required": ["story_slot", "story_type", "styles", "idea", "hook", "sticker", "pillar", "goal", "why"],
                "additionalProperties": False}},
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slot": {"type": "integer", "enum": [s.index for s in slots]},
                        "theme": {"type": "string", "enum": THEMES},
                        "format": {"type": "string", "enum": FORMATS},
                        "styles": {"type": "array", "items": {"type": "string", "enum": style_ids}},
                        "idea": {"type": "string"},
                        "hook": {"type": "string"},
                        "why": {"type": "string"},
                        "pillar": {"type": "string"},
                        "goal": {"type": "string", "enum": GOALS},
                        "tagline": {"type": "string"},
                        "cta": {"type": "string"},
                        "direction": {"type": "string"},
                        "boost": {"type": "boolean"},
                        "boost_budget": {"type": "number"},
                    },
                    "required": ["slot", "theme", "format", "styles", "idea", "hook", "why", "pillar", "goal",
                                 "tagline", "cta", "direction", "boost", "boost_budget"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "pillars", "posts", "stories"],
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

def make_plan(brand: Brand, start: date | None = None, client=None, note: str = "", days: int | None = None,
              stories: bool = True, context: str = "") -> dict:
    """Plan `days` days from `start` (default: the Brand page plan length). A month plan is the Content Calendar."""
    from app.team import working
    label = "Monthly content calendar" if (days or 0) >= 21 else "Weekly content plan"
    with working(brand, "planner", label) as job:
        result = _make_plan(brand, start, client, note, days, stories, context)
        job["result"], job["cost_usd"] = f"{len(result['rows'])} items planned", result["cost_usd"]
        return result


def current_pillars(brand: Brand) -> list[dict]:
    from app.db import get_state
    return json.loads(get_state(brand.slug, "calendar:pillars") or "[]")


def _make_plan(brand: Brand, start: date | None = None, client=None, note: str = "", days: int | None = None,
               with_stories: bool = True, context: str = "") -> dict:
    """Make and save the plan. Returns {"summary", "rows", "cost_usd", "model"}."""
    tz = ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata"))
    start = start or (datetime.now(tz).date() + timedelta(days=1))
    days = days or int(brand.config["schedule"].get("plan_horizon_days", 7))
    styles = catalog_styles(brand)
    if not styles:
        raise PlanError("No products to plan with yet. Press “Get products from Shopify” first "
                        "(or check that products are active and in stock).")
    slots = plan_slots(brand, start, days)
    if not slots:
        raise PlanError("No posting days are ticked. Choose them on Brand → Posting days.")
    stories = story_slots(brand, start, days) if with_stories else []
    pillars = current_pillars(brand) if days < 21 else []  # a new month decides fresh pillars
    themes, formats, reel_cap = targets(brand, len(slots), days)
    end = start + timedelta(days=days - 1)
    festivals = calendar_india.upcoming(start, end, (brand.config.get("festivals") or {}).get("custom_dates"))

    try:
        client = client or claude.client_for(brand)
    except claude.ClaudeError as e:
        raise PlanError(str(e)) from None

    messages = [{"role": "user", "content": user_prompt(
        brand, styles, slots, themes, formats, reel_cap, festivals, performance_summary(brand), recent_ideas(brand),
        stories, pillars, context)
        + (f"\n\nThe owner's request for this plan (follow it, within the rules): {note.strip()}" if note.strip() else "")}]
    styles_by_id = {s["id"]: s for s in styles}
    cost, model_used, plan = 0.0, claude.MODEL, None
    for attempt in range(2):
        try:
            plan, call_cost, model_used = claude.ask(client, system_prompt(brand), messages,
                                                     plan_schema(styles, slots, stories),
                                                     max_tokens=20000 if days >= 21 else 16000)
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

    rows = _save(brand, plan, slots, styles_by_id, start, reel_cap, stories, end)
    if plan.get("pillars"):
        set_state(brand.slug, "calendar:pillars", json.dumps(plan["pillars"], ensure_ascii=False))
    if days >= 21:
        set_state(brand.slug, "calendar:summary", json.dumps({"start": start.isoformat(), "end": end.isoformat(),
                                                              "summary": plan["summary"]}, ensure_ascii=False))
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


def _save(brand: Brand, plan: dict, slots: list[Slot], styles_by_id: dict, start: date, reel_cap: int,
          stories: list[Slot] | None = None, end: date | None = None) -> list[dict]:
    by_slot = {s.index: s for s in slots}
    posts = sorted(plan["posts"], key=lambda p: p["slot"])
    reels = 0
    tz = by_slot[posts[0]["slot"]].when.tzinfo
    now = datetime.now(tz).isoformat(timespec="seconds")
    period_end = datetime.combine((end or slots[-1].when.date()) + timedelta(days=1), datetime.min.time(), tz)
    rows = []
    with connect() as conn:
        # A new plan replaces only what is still just "planned" in the same period (the rest of the month stays).
        conn.execute("DELETE FROM content_plan WHERE brand = ? AND status = 'planned' AND scheduled_for >= ? "
                     "AND scheduled_for < ?", (brand.slug, datetime.combine(start, datetime.min.time(), tz).isoformat(),
                                               period_end.isoformat()))
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
                                     "styles": [styles_by_id[s]["title"] for s in p["styles"]],
                                     **_extras(p)}, ensure_ascii=False),
                "status": "planned", "created_at": now,
            }
            cur = conn.execute(f"INSERT INTO content_plan ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                               list(row.values()))
            rows.append({**row, "id": cur.lastrowid})
        by_story = {s.index: s for s in stories or []}
        used = set()
        for st in plan.get("stories") or []:
            slot = by_story.get(st.get("story_slot"))
            if not slot or slot.index in used:
                continue  # unknown or double-booked story time
            used.add(slot.index)
            sids = [x for x in st.get("styles", []) if x in styles_by_id]
            warnings = check_post({"styles": sids, "idea": st["idea"], "hook": st["hook"]}, styles_by_id, brand)
            row = {
                "brand": brand.slug, "week_start": start.isoformat(), "scheduled_for": slot.when.isoformat(),
                "theme": "story", "format": "story",
                "product_ids": json.dumps([pid for sid in sids for pid in styles_by_id[sid]["product_ids"][:1]]),
                "idea": st["idea"].strip(), "hook": st["hook"].strip(),
                "notes": json.dumps({"why": st.get("why", ""), "warnings": warnings,
                                     "styles": [styles_by_id[s]["title"] for s in sids],
                                     "story_type": st.get("story_type"), "sticker": st.get("sticker", ""),
                                     "pillar": st.get("pillar", ""), "goal": st.get("goal", "")}, ensure_ascii=False),
                "status": "planned", "created_at": now,
            }
            cur = conn.execute(f"INSERT INTO content_plan ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                               list(row.values()))
            rows.append({**row, "id": cur.lastrowid})
    return rows


def _extras(p: dict) -> dict:
    """The calendar columns for one feed post (missing ones are simply left out)."""
    out = {k: p[k] for k in ("pillar", "goal", "tagline", "cta", "direction") if p.get(k)}
    if p.get("boost"):
        out["boost"] = True
        out["boost_budget"] = max(0, float(p.get("boost_budget") or 0))
    return out


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
                    **{k: notes.get(k) for k in ("pillar", "goal", "tagline", "cta", "direction", "boost",
                                                  "boost_budget", "story_type", "sticker", "idea_ok")},
                    "format_label": FORMAT_LABELS.get(r["format"], r["format"])})
    return out


def price_text(p: dict) -> str:
    """'₹249' or 'from ₹249' — straight from Shopify."""
    if p["max_price"] and p["max_price"] > p["min_price"]:
        return f"from ₹{p['min_price']:g}"
    return f"₹{p['min_price']:g}"
