"""Learning from results: post numbers from Instagram and Facebook, the weekly report,
and the lessons that feed into the next week's plan.

Numbers are collected every night for posts from the last 30 days (posts keep
getting views for a while). Once a week Claude reads the numbers and writes 3–5
plain-English lessons ("carousels about fabric care get saved most"), which the
planner then follows.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import claude, publisher
from app.brands import Brand
from app.db import connect, get_state, set_state

log = logging.getLogger(__name__)

# Current metric names (Meta docs, Oct 2026). Meta retires metrics often, so any name Meta rejects is skipped.
IG_FEED_METRICS = ["reach", "likes", "comments", "saved", "shares", "views", "total_interactions", "profile_visits",
                   "follows"]
IG_REEL_METRICS = ["reach", "likes", "comments", "saved", "shares", "views", "ig_reels_avg_watch_time"]
FB_POST_METRICS = ["post_total_media_view_unique", "post_media_view", "post_clicks"]
_rejected: set[str] = set()  # metric names Meta refused this session


def _insight_values(brand: Brand, object_id: str, metrics: list[str], token: str) -> dict:
    """Ask for several metrics at once; if Meta rejects the batch, ask one by one and skip the bad ones."""
    base = publisher._base(brand)
    wanted = [m for m in metrics if m not in _rejected]
    if not wanted:
        return {}
    try:
        body = publisher._call("GET", f"{base}/{object_id}/insights", token, metric=",".join(wanted))
        return _parse(body)
    except publisher.PublishError:
        values = {}
        for m in wanted:
            try:
                values.update(_parse(publisher._call("GET", f"{base}/{object_id}/insights", token, metric=m)))
            except publisher.PublishError:
                _rejected.add(m)
                log.info("Meta doesn't offer the metric %s here; skipping it", m)
        return values


def _parse(body: dict) -> dict:
    out = {}
    for item in body.get("data", []):
        if item.get("values"):
            value = item["values"][0].get("value")
        else:
            value = (item.get("total_value") or {}).get("value")
        if isinstance(value, dict):  # e.g. reactions by type
            value = sum(v for v in value.values() if isinstance(v, (int, float)))
        if value is not None:
            out[item["name"]] = value
    return out


def fetch_post_numbers(brand: Brand, post: dict, is_reel: bool) -> dict:
    """Numbers for one published post, in the agent's own names."""
    token = publisher.page_token(brand)
    pid = post["platform_post_id"]
    if post["platform"] == "instagram":
        raw = _insight_values(brand, pid, IG_REEL_METRICS if is_reel else IG_FEED_METRICS, token)
        return {"reach": raw.get("reach"), "impressions": raw.get("views"), "likes": raw.get("likes"),
                "comments": raw.get("comments"), "saves": raw.get("saved"), "shares": raw.get("shares"),
                "profile_visits": raw.get("profile_visits"), "link_clicks": None, "raw": raw}
    raw = _insight_values(brand, pid, FB_POST_METRICS, token)
    try:  # likes/comments/shares are plain fields on the post, not "insights"
        fields = publisher._call("GET", f"{publisher._base(brand)}/{pid}", token,
                                 fields="reactions.summary(total_count),comments.summary(total_count),shares")
    except publisher.PublishError:
        fields = {}
    raw["fields"] = fields
    return {"reach": raw.get("post_total_media_view_unique"), "impressions": raw.get("post_media_view"),
            "likes": ((fields.get("reactions") or {}).get("summary") or {}).get("total_count"),
            "comments": ((fields.get("comments") or {}).get("summary") or {}).get("total_count"),
            "saves": None, "shares": (fields.get("shares") or {}).get("count"), "profile_visits": None,
            "link_clicks": raw.get("post_clicks"), "raw": raw}


def collect(brand: Brand, days: int = 30) -> int:
    """Fetch fresh numbers for every post published in the last `days` days. Returns how many were updated."""
    since = (datetime.now(ZoneInfo("UTC")) - timedelta(days=days)).isoformat()
    with connect() as conn:
        posts = [dict(r) for r in conn.execute(
            "SELECT p.*, d.format FROM posts p JOIN drafts d ON d.id = p.draft_id WHERE p.brand = ? "
            "AND p.status = 'published' AND p.platform_post_id IS NOT NULL AND p.published_at >= ?",
            (brand.slug, since[:10]))]
    updated = 0
    now = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
    for post in posts:
        try:
            n = fetch_post_numbers(brand, post, is_reel=post["format"] == "gemini_reel")
        except publisher.PublishError as e:
            log.warning("Couldn't get numbers for draft #%d on %s: %s", post["draft_id"], post["platform"], e)
            continue
        with connect() as conn:
            conn.execute(
                "INSERT INTO insights (brand, post_id, collected_at, reach, impressions, likes, comments, saves, shares, "
                "profile_visits, link_clicks, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (brand.slug, post["id"], now, n["reach"], n["impressions"], n["likes"], n["comments"], n["saves"],
                 n["shares"], n["profile_visits"], n["link_clicks"], json.dumps(n["raw"])))
        updated += 1
    log.info("Collected numbers for %d post(s)", updated)
    return updated


# ---------------------------------------------------------------- reading the numbers

def latest(brand: Brand, days: int = 7) -> list[dict]:
    """The newest numbers for each post published in the last `days` days, with what the post was."""
    since = (datetime.now(ZoneInfo("UTC")) - timedelta(days=days)).isoformat()[:10]
    with connect() as conn:
        rows = conn.execute("""
            SELECT i.*, p.platform, p.permalink, p.published_at, p.draft_id, p.scheduled_for,
                   d.format, d.theme, d.image_style, d.caption, cp.idea
            FROM insights i
            JOIN (SELECT post_id, MAX(id) AS last_id FROM insights GROUP BY post_id) li ON li.last_id = i.id
            JOIN posts p ON p.id = i.post_id JOIN drafts d ON d.id = p.draft_id
            LEFT JOIN content_plan cp ON cp.id = d.plan_id
            WHERE i.brand = ? AND p.published_at >= ? ORDER BY p.published_at DESC""",
                            (brand.slug, since)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["interactions"] = sum(d[k] or 0 for k in ("likes", "comments", "saves", "shares"))
        d["rate"] = (d["interactions"] / d["reach"]) if d["reach"] else None
        d["when"] = datetime.fromisoformat(d["scheduled_for"])
        out.append(d)
    return out


def _group(rows: list[dict], key) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    result = []
    for name, items in groups.items():
        reach = [i["reach"] for i in items if i["reach"] is not None]
        rates = [i["rate"] for i in items if i["rate"] is not None]
        result.append({"name": name, "posts": len(items),
                       "avg_reach": sum(reach) / len(reach) if reach else None,
                       "avg_rate": sum(rates) / len(rates) if rates else None,
                       "saves": sum(i["saves"] or 0 for i in items), "shares": sum(i["shares"] or 0 for i in items)})
    return sorted(result, key=lambda g: (g["avg_rate"] or 0), reverse=True)


def report(brand: Brand, days: int = 7) -> dict:
    rows = latest(brand, days)
    label = {"template_image": "Brand image", "carousel": "Carousel", "gemini_image": "AI image", "gemini_reel": "Reel"}
    ranked = sorted([r for r in rows if r["rate"] is not None], key=lambda r: r["rate"], reverse=True)
    return {
        "rows": rows,
        "totals": {p: {"posts": len(g), "reach": sum(r["reach"] or 0 for r in g),
                       "interactions": sum(r["interactions"] for r in g)}
                   for p, g in _bucket(rows, "platform").items()},
        "by_format": _group(rows, lambda r: label.get(r["format"], r["format"])),
        "by_theme": _group(rows, lambda r: (r["theme"] or "other").title()),
        "by_style": _group([r for r in rows if r["format"] in ("gemini_image", "gemini_reel")],
                           lambda r: (r["image_style"] or "").replace("_", " ")),
        "by_day": _group(rows, lambda r: r["when"].strftime("%a %I %p").replace(" 0", " ")),
        "best": ranked[:3], "worst": ranked[-2:] if len(ranked) > 4 else [],
    }


def _bucket(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


# ---------------------------------------------------------------- lessons for the planner

LESSONS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "worked": {"type": "array", "items": {"type": "string"}},
        "try_next": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "worked", "try_next"],
    "additionalProperties": False,
}


def numbers_text(rep: dict) -> str:
    def groups(title, items):
        lines = [f"{title}:"] + [f"- {g['name']}: {g['posts']} posts, avg reach {g['avg_reach'] or 0:.0f}, "
                                 f"engagement {100 * (g['avg_rate'] or 0):.1f}%, saves {g['saves']}, shares {g['shares']}"
                                 for g in items]
        return "\n".join(lines)
    posts = "\n".join(f"- {r['when']:%a %d %b %H:%M} {r['platform']} {r['format']}/{r['theme']}"
                      f"{'/' + r['image_style'] if r['image_style'] and r['image_style'] != 'template' else ''}: "
                      f"reach {r['reach']}, likes {r['likes']}, comments {r['comments']}, saves {r['saves']}, "
                      f"shares {r['shares']} — idea: {(r['idea'] or (r['caption'] or '')[:80])}" for r in rep["rows"])
    return "\n\n".join([groups("By format", rep["by_format"]), groups("By topic", rep["by_theme"]),
                        groups("By AI image style", rep["by_style"]), groups("By posting time", rep["by_day"]),
                        "Each post:\n" + posts])


def make_lessons(brand: Brand, client=None) -> dict | None:
    """Claude turns the last 4 weeks of numbers into a few lessons. None if there isn't enough data yet."""
    rep = report(brand, days=28)
    if len([r for r in rep["rows"] if r["reach"]]) < 3:
        return None
    client = client or claude.client_for(brand)
    system = (f"You are the social media analyst for {brand.name}, an innerwear brand. You read post results and write "
              "short, practical lessons for next week's content plan. Be specific (formats, topics, image styles, "
              "posting times, product styles). Base every lesson on the numbers; say when a sample is too small to be "
              "sure. Never suggest suggestive content, fake offers or anything against the brand rules.")
    user = ("Results for posts from the last 4 weeks (engagement = likes+comments+saves+shares divided by reach; "
            "saves and shares matter most):\n\n" + numbers_text(rep) +
            "\n\nReturn a one-line headline, 2–4 things that worked, and 2–3 things to try next week.")
    answer, cost, _ = claude.ask(client, system, [{"role": "user", "content": user}], LESSONS_SCHEMA, effort="medium",
                                 max_tokens=8000)
    answer["date"] = datetime.now().strftime("%d %b %Y")
    answer["cost_usd"] = round(cost, 4)
    set_state(brand.slug, "lessons", json.dumps(answer))
    log.info("New lessons from results: %s", answer["headline"])
    return answer


def lessons(brand: Brand) -> dict | None:
    raw = get_state(brand.slug, "lessons")
    return json.loads(raw) if raw else None


def for_planner(brand: Brand) -> str:
    """What the planner reads about past performance."""
    rep = report(brand, days=28)
    if not rep["rows"]:
        return "No post results yet — this is one of the first plans. Use a balanced, varied mix."
    text = numbers_text(rep)
    les = lessons(brand)
    if les:
        text += ("\n\nLessons from the analyst (" + les["date"] + "): " + les["headline"] + "\nWorked: "
                 + "; ".join(les["worked"]) + "\nTry next: " + "; ".join(les["try_next"]))
    return text
