"""🔎 Market Researcher: competitors' Instagram posts (official Business Discovery API), web research
on trends and timing (Claude web search), and your own catalogue — written up as a weekly market brief.

Competitor data comes only from Meta's official API for public business/creator accounts: captions, post
type, likes and comments. Nothing is scraped. Reach and saves of other accounts are private, so they're
never claimed.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from statistics import mean

from app import claude, goals, planner, publisher
from app.brands import Brand
from app.db import get_state, set_state
from app.team import AGENTS, competitors, latest, post_brief, say, skill, timed

log = logging.getLogger(__name__)
POSTS_PER_COMPETITOR = 15


def _fields() -> str:
    media = ("caption,like_count,comments_count,media_type,media_product_type,timestamp,permalink")
    return f"username,name,followers_count,media_count,media.limit({POSTS_PER_COMPETITOR}){{{media}}}"


def scan_competitor(brand: Brand, handle: str) -> dict:
    """Recent posts of one public business/creator account, with which ones beat that account's own average."""
    ig_id = brand.config["meta"].get("instagram_account_id")
    if not ig_id:
        raise publisher.PublishError("Instagram isn't connected yet (Connect accounts → Instagram + Facebook).")
    token = publisher.page_token(brand)
    body = publisher._call("GET", f"{publisher._base(brand)}/{ig_id}", token,
                           fields=f"business_discovery.username({handle}){{{_fields()}}}")
    bd = body.get("business_discovery") or {}
    posts = []
    for m in (bd.get("media") or {}).get("data", []):
        likes, comments = m.get("like_count") or 0, m.get("comments_count") or 0
        kind = "reel" if m.get("media_product_type") == "REELS" else \
            {"CAROUSEL_ALBUM": "carousel", "VIDEO": "video"}.get(m.get("media_type"), "image")
        posts.append({"type": kind, "likes": likes, "comments": comments, "engagement": likes + 2 * comments,
                      "when": (m.get("timestamp") or "")[:10], "url": m.get("permalink"),
                      "caption": (m.get("caption") or "")[:300]})
    avg = mean(p["engagement"] for p in posts) if posts else 0
    for p in posts:
        p["vs_avg"] = round(p["engagement"] / avg, 2) if avg else None
    days = None
    if len(posts) >= 2:
        first, last = posts[-1]["when"], posts[0]["when"]
        try:
            span = (datetime.fromisoformat(last) - datetime.fromisoformat(first)).days or 1
            days = round(7 * (len(posts) - 1) / span, 1)
        except ValueError:
            pass
    return {"handle": handle, "name": bd.get("name"), "followers": bd.get("followers_count"),
            "posts_per_week": days, "avg_engagement": round(avg, 1),
            "formats": {k: sum(1 for p in posts if p["type"] == k) for k in ("reel", "carousel", "image", "video")},
            "top_posts": sorted(posts, key=lambda p: p["engagement"], reverse=True)[:4], "posts": posts}


FIND_SCHEMA = {
    "type": "object",
    "properties": {"competitors": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"},
        "instagram": {"type": "string", "description": "Instagram username without @, or empty if not found"},
        "website": {"type": "string"},
        "price_note": {"type": "string", "description": "Typical pack price / price per piece, with source"},
        "why": {"type": "string", "description": "Why it competes with us (same products, price, audience)"}},
        "required": ["name", "instagram", "website", "price_note", "why"], "additionalProperties": False}}},
    "required": ["competitors"], "additionalProperties": False,
}
REFIND_AFTER_DAYS = 30


def found_competitors(brand: Brand) -> dict:
    """Competitors the Researcher found by itself: {"found_at": date, "list": [...]}."""
    return json.loads(get_state(brand.slug, "team:competitors_auto") or '{"found_at": null, "list": []}')


@timed("researcher", "Finding competitors")
def find_competitors(brand: Brand, client=None) -> dict:
    """Search the web for the brands we really compete with (same products, similar prices, sold on Instagram)."""
    client = client or claude.client_for(brand)
    prompt = (f"Find the 8–10 brands that compete most directly with {brand.name} "
              f"({brand.config['brand'].get('website', '')}): Indian D2C brands selling men's innerwear (briefs, "
              f"trunks, boxers, vests), especially multi-packs at similar prices, that advertise on Instagram. Our "
              f"products:\n{_catalogue(brand)}\n\nFor each: brand name, its official Instagram username (check it "
              "on the brand's own website or a reliable page — don't guess), website, typical pack price and price "
              "per piece (with the source), and why it competes with us. Include both big names and fast-growing "
              "small ones.")
    notes, cost, searches = claude.research(client, _system(brand), prompt, max_searches=10)
    data, ask_cost, _ = claude.ask(client, _system(brand), [{"role": "user", "content":
                                   f"Turn these findings into the list. Leave instagram empty when the notes don't "
                                   f"give a confirmed username.\n\n{notes}"}], FIND_SCHEMA, effort="low")
    for c in data["competitors"]:
        c["instagram"] = c["instagram"].strip().lstrip("@").split("instagram.com/")[-1].split("?")[0].strip("/").lower()
    result = {"found_at": datetime.now().date().isoformat(), "list": data["competitors"][:12]}
    set_state(brand.slug, "team:competitors_auto", json.dumps(result, ensure_ascii=False))
    log.info("Researcher found %d competitors by itself (%d searches)", len(result["list"]), searches)
    return {**result, "cost_usd": cost + ask_cost,
            "summary": f"Found {len(result['list'])} competitors: " + ", ".join(c["name"] for c in result["list"])}


def watch_list(brand: Brand) -> list[str]:
    """Instagram handles to read: yours first, then the ones the Researcher found (max 12)."""
    handles = list(competitors(brand))
    for c in found_competitors(brand)["list"]:
        if c.get("instagram") and c["instagram"] not in handles:
            handles.append(c["instagram"])
    return handles[:12]


def scan_all(brand: Brand) -> tuple[list[dict], list[str]]:
    found, problems = [], []
    for handle in watch_list(brand):
        try:
            found.append(scan_competitor(brand, handle))
        except publisher.PublishError as e:
            problems.append(f"@{handle}: {e}")
            log.info("Competitor @%s not readable: %s", handle, e)
    return found, problems


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence: the most useful thing this week."},
        "insights": {"type": "array", "items": {"type": "object", "properties": {
            "insight": {"type": "string"}, "evidence": {"type": "string", "description": "numbers, post links, sources"},
            "confidence": {"type": "string", "enum": ["strong", "medium", "weak"]},
            "use_it": {"type": "string", "description": "what the Planner/Creator should do with it"}},
            "required": ["insight", "evidence", "confidence", "use_it"], "additionalProperties": False}},
        "hooks_to_try": {"type": "array", "items": {"type": "string"}},
        "formats_working": {"type": "array", "items": {"type": "string"}},
        "dates_coming": {"type": "array", "items": {"type": "string"}},
        "customer_words": {"type": "array", "items": {"type": "string"}},
        "watch_out": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "insights", "hooks_to_try", "formats_working", "dates_coming", "customer_words",
                 "watch_out"],
    "additionalProperties": False,
}


def _system(brand: Brand) -> str:
    return (f"You are the {AGENTS['researcher']['name']} in the social media team of {brand.name}, an Indian "
            f"innerwear brand ({brand.config['brand'].get('website', '')}). Follow your playbook.\n\n"
            f"{skill(brand, 'researcher')}\n\nBRAND RULES:\n{brand.rules}")


def _catalogue(brand: Brand) -> str:
    return "\n".join(f"- {s['title']} ({s['pack_size'] or '?'}-pack, {planner.price_text({'min_price': s['price_from'], 'max_price': s['price_to']})}"
                     f", {s['colourways']} colours)" for s in planner.catalog_styles(brand)[:25]) or "(no products synced)"


@timed("researcher", "Market research")
def run(brand: Brand, client=None, web: bool = True) -> dict:
    """Write this week's market brief. Returns the stored brief."""
    client = client or claude.client_for(brand)
    cost, extra_problems = 0.0, []
    known = found_competitors(brand)
    stale = not known["found_at"] or \
        (datetime.now().date() - datetime.fromisoformat(known["found_at"]).date()).days >= REFIND_AFTER_DAYS
    if web and stale:  # find competitors by itself (monthly), so it never waits for you to list them
        try:
            cost += find_competitors(brand, client)["cost_usd"]
        except claude.ClaudeError as e:
            extra_problems.append(f"finding competitors: {e}")
    rivals = found_competitors(brand)["list"]
    scans, problems = scan_all(brand)
    problems += extra_problems
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%A %d %B %Y")
    web_notes, searches = "", 0
    if web:
        prompt = (f"Today is {today}. Research, for {brand.name} (men's innerwear packs sold online in India, "
                  f"mostly through Instagram and Facebook ads):\n"
                  "1. What content formats and hooks are working right now for Indian D2C innerwear/apparel brands "
                  "on Instagram (reels, carousels, UGC-style).\n"
                  "2. Festivals, sales and seasons in the next 6 weeks that matter for innerwear in India.\n"
                  "3. What customers praise or complain about in men's innerwear (comfort, elastic, fabric, fit, "
                  "value).\n"
                  "4. Any recent change to Meta/Instagram that affects small brands (reels reach, ads, policies).\n"
                  "5. Our competitors right now — "
                  + (", ".join(c["name"] for c in rivals) if rivals else "the main Indian men's innerwear D2C brands")
                  + ": their current offers and pack prices, new launches, the ad angles and content themes they "
                  "push (Meta Ad Library, their Instagram and websites), and customer complaints about them.\n"
                  "Search the web. Give short findings, each with its source link. Say when something is uncertain.")
        try:
            web_notes, web_cost, searches = claude.research(client, _system(brand), prompt, max_searches=12)
            cost += web_cost
        except claude.ClaudeError as e:
            web_notes = f"(web research failed: {e})"
    comp_text = json.dumps([{k: v for k, v in c.items() if k != "posts"} for c in scans], ensure_ascii=False)[:30000]
    user = (f"Today is {today}. Write this week's market brief for the team.\n\n"
            f"OUR GOALS AND LIMITS:\n{goals.summary_for_agents(brand)}\n\n"
            f"OUR PRODUCTS (from Shopify):\n{_catalogue(brand)}\n\n"
            f"COMPETITORS WE FOUND (name, Instagram, price): "
            f"{json.dumps(rivals, ensure_ascii=False)[:6000] if rivals else '(none yet)'}\n\n"
            f"COMPETITORS' INSTAGRAM POSTS (official Instagram data; 'vs_avg' = engagement ÷ that account's average; engagement = "
            f"likes + 2×comments):\n{comp_text if scans else '(none set or none readable)'}\n"
            + (f"Couldn't read: {'; '.join(problems)}\n" if problems else "")
            + f"\nWEB RESEARCH NOTES:\n{web_notes or '(not run)'}\n\n"
            "Only use evidence from the data above. Where there's little data, say so and keep insights few.")
    data, ask_cost, _ = claude.ask(client, _system(brand), [{"role": "user", "content": user}], BRIEF_SCHEMA,
                                   effort="medium")
    cost += ask_cost
    body = _body(data, scans, problems)
    brief_id = post_brief(brand, "researcher", "market", data["headline"][:200], body,
                          {**data, "competitors": [{k: v for k, v in c.items() if k != "posts"} for c in scans],
                           "problems": problems, "web_notes": web_notes[:8000], "searches": searches}, cost)
    log.info("Market brief written (%d competitors, %d web searches, about US$%.2f)", len(scans), searches, cost)
    return {"id": brief_id, "data": data, "cost_usd": cost, "body": body}


def _body(data: dict, scans: list[dict], problems: list[str]) -> str:
    lines = [data["headline"], ""]
    for i in data["insights"]:
        lines.append(f"• {i['insight']} ({i['confidence']}) — {i['evidence']}\n  → {i['use_it']}")
    for title, key in (("Hooks to try", "hooks_to_try"), ("Formats working", "formats_working"),
                       ("Dates coming", "dates_coming"), ("Customer words", "customer_words"),
                       ("Watch out", "watch_out")):
        if data.get(key):
            lines += ["", f"{title}:"] + [f"- {x}" for x in data[key]]
    if scans:
        lines += ["", "Competitors read: " + ", ".join(f"@{c['handle']}" for c in scans)]
    if problems:
        lines += ["Couldn't read: " + "; ".join(problems)]
    return "\n".join(lines)


def tell_owner(brand: Brand, result: dict) -> None:
    d = result["data"]
    from app.telegram import esc
    top = "\n".join(f"• {esc(i['insight'])}" for i in d["insights"][:3])
    say(brand, "researcher", f"Market brief: <b>{esc(d['headline'])}</b>\n{top}\n<i>Full brief: Team page.</i>")


# ---------------------------------------------------------------- reviewing the Planner's plan

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["good", "revise"]},
                   "notes": {"type": "array", "items": {"type": "string"}},
                   "revision_request": {"type": "string",
                                        "description": "If revise: exact changes for the Planner, else empty."}},
    "required": ["verdict", "notes", "revision_request"], "additionalProperties": False,
}


@timed("researcher", "Reviewing the Planner's plan")
def review_plan(brand: Brand, rows: list[dict], client=None) -> tuple[dict, float]:
    """One critique of the Planner's week from the market's point of view (the Planner revises at most once)."""
    client = client or claude.client_for(brand)
    market = latest(brand, "market")
    plan = "\n".join(f"- {r['when']:%a %d %b %I:%M %p} {r['format_label']} · {r['theme']} · "
                     f"{', '.join(p['title'] for p in r['products'])} · idea: {r['idea']} · hook: {r.get('hook') or ''}"
                     for r in rows)
    user = (f"Review this week's plan against the latest market brief. Approve it ('good') unless something "
            f"clearly hurts results (e.g. ignores a strong signal, repeats a format that's tired, misses a key date, "
            f"weak hooks). Max 4 notes.\n\nMARKET BRIEF:\n{market['body'] if market else '(none yet)'}\n\nPLAN:\n{plan}")
    data, cost, _ = claude.ask(client, _system(brand), [{"role": "user", "content": user}], REVIEW_SCHEMA,
                               effort="low")
    return data, cost
