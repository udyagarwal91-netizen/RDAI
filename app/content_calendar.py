"""The Content Calendar: the Planner's month (reels, posts, Stories) in one place, like your team's spreadsheet —
date and time, type, pillar, goal, hook, product (prices from Shopify), tagline, CTA / sticker, creative
direction, boost and status — plus what you can do to each item: approve the idea, edit it, ask the
Planner to redo it, skip it, or have the Creator make it now.

Status of an item, from idea to posted:
  Idea → Idea ✅ (you liked it) → Ready for your OK (draft made) → Scheduled (draft approved) → Posted.
"""

import calendar as _cal
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import claude, factcheck, planner
from app.brands import Brand
from app.db import connect

log = logging.getLogger(__name__)
TYPES = {"gemini_reel": "Reel", "story": "Story"}  # everything else is a Post
EDITABLE = ("idea", "hook", "tagline", "cta", "direction", "sticker")


def kind(fmt: str) -> str:
    return TYPES.get(fmt, "Post")


def next_month_start(today: date) -> date:
    return (today.replace(day=28) + timedelta(days=4)).replace(day=1)


def month_days(start: date) -> int:
    return _cal.monthrange(start.year, start.month)[1] - start.day + 1


def _status(row: dict, draft: dict | None) -> tuple[str, str]:
    """(label, key) for the calendar colour."""
    if row["status"] == "skipped":
        return "Skipped", "skipped"
    if draft:
        return {"pending": ("Ready for your OK", "ready"), "approved": ("Scheduled", "scheduled"),
                "published": ("Posted", "posted"), "rejected": ("Rejected", "skipped"),
                "regenerating": ("Being remade", "making")}.get(draft["status"], (draft["status"], "making"))
    notes = json.loads(row["notes"] or "{}")
    if row["format"] == "story":
        return ("Idea ✅" if notes.get("idea_ok") else "Idea") + " · story", "idea"
    return ("Idea ✅", "approved") if notes.get("idea_ok") else ("Idea", "idea")


def items(brand: Brand, start: date, end: date) -> list[dict]:
    """Every calendar item from start to end (inclusive), with products, status and its draft (if any)."""
    tz = ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata"))
    lo = datetime.combine(start, datetime.min.time(), tz).isoformat()
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tz).isoformat()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM content_plan WHERE brand = ? AND scheduled_for >= ? AND scheduled_for < ? "
            "ORDER BY scheduled_for", (brand.slug, lo, hi))]
        drafts = {r["plan_id"]: dict(r) for r in conn.execute(
            "SELECT id, plan_id, status, caption, hashtags FROM drafts WHERE brand = ? AND plan_id IS NOT NULL "
            "ORDER BY id", (brand.slug,))}
        products = {r["shopify_id"]: dict(r) for r in conn.execute("SELECT * FROM products WHERE brand = ?",
                                                                    (brand.slug,))}
    out = []
    for r in rows:
        notes = json.loads(r["notes"] or "{}")
        prods = []
        for pid in json.loads(r["product_ids"] or "[]"):
            p = products.get(pid)
            if p:
                images = json.loads(p["images"] or "[]")
                prods.append({"title": p["title"], "pack_size": p["pack_size"], "min_price": p["min_price"],
                              "max_price": p["max_price"], "image": images[0] if images else None,
                              "price": planner.price_text(p)})
        d = drafts.get(r["id"])
        label, key = _status(r, d)
        when = datetime.fromisoformat(r["scheduled_for"])
        out.append({**r, **{k: notes.get(k) for k in ("pillar", "goal", "tagline", "cta", "direction", "boost",
                                                      "boost_budget", "story_type", "sticker", "why", "idea_ok")},
                    "warnings": notes.get("warnings", []), "when": when, "day": when.date(),
                    "type": kind(r["format"]), "format_label": planner.FORMAT_LABELS.get(r["format"], r["format"]),
                    "products": prods, "draft": d, "status_label": label, "status_key": key,
                    "caption": (d or {}).get("caption"), "hashtags": json.loads((d or {}).get("hashtags") or "[]")})
    return out


def month(brand: Brand, year: int, month_no: int) -> dict:
    """A month for the Calendar page: weeks of days (Mon–Sun) with their items, plus summary counts."""
    first = date(year, month_no, 1)
    last = date(year, month_no, _cal.monthrange(year, month_no)[1])
    all_items = items(brand, first, last)
    by_day: dict[date, list] = {}
    for it in all_items:
        by_day.setdefault(it["day"], []).append(it)
    weeks = [[{"day": d, "items": by_day.get(d, []), "in_month": d.month == month_no} for d in week]
             for week in _cal.Calendar(firstweekday=0).monthdatescalendar(year, month_no)]
    count = lambda key: _counts([it.get(key) or "—" for it in all_items if it["status"] != "skipped"])  # noqa: E731
    return {"first": first, "last": last, "weeks": weeks, "items": all_items,
            "by_type": _counts([it["type"] for it in all_items if it["status"] != "skipped"]),
            "by_pillar": count("pillar"), "by_goal": count("goal"), "by_status": _counts(
                [it["status_label"].split(" · ")[0] for it in all_items]),
            "boosts": [it for it in all_items if it.get("boost")]}


def _counts(values: list[str]) -> list[tuple[str, int]]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return sorted(out.items(), key=lambda kv: -kv[1])


# ---------------------------------------------------------------- changing one item

def _row(brand: Brand, plan_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM content_plan WHERE brand = ? AND id = ?", (brand.slug, plan_id)).fetchone()
    return dict(r) if r else None


def _write(plan_id: int, row: dict, notes: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE content_plan SET idea = ?, hook = ?, notes = ?, status = ? WHERE id = ?",
                     (row["idea"], row["hook"], json.dumps(notes, ensure_ascii=False), row["status"], plan_id))


class CalendarError(Exception):
    pass


def approve_idea(brand: Brand, plan_id: int) -> None:
    row = _row(brand, plan_id) or _missing()
    notes = json.loads(row["notes"] or "{}")
    notes["idea_ok"] = True
    _write(plan_id, row, notes)


def skip(brand: Brand, plan_id: int) -> None:
    row = _row(brand, plan_id) or _missing()
    if row["status"] != "planned":
        raise CalendarError("This item already has a draft — reject the draft on the Drafts page instead.")
    row["status"] = "skipped"
    _write(plan_id, row, json.loads(row["notes"] or "{}"))


def edit(brand: Brand, plan_id: int, fields: dict) -> list[str]:
    """Save your changes to an item. Returns warnings (e.g. a price not in Shopify)."""
    row = _row(brand, plan_id) or _missing()
    notes = json.loads(row["notes"] or "{}")
    for k in EDITABLE:
        if k in fields:
            value = (fields.get(k) or "").strip()[:600]
            if k in ("idea", "hook"):
                row[k] = value or row[k]
            else:
                notes[k] = value
    _, prices, packs = _facts(brand, row)
    notes["warnings"] = factcheck.check_text(f"{row['idea']} {row['hook']} {notes.get('tagline', '')}", brand,
                                             prices, packs)
    notes["edited_by_owner"] = True
    _write(plan_id, row, notes)
    return notes["warnings"]


def _facts(brand: Brand, row: dict):
    from app import producer, writer
    return writer.product_facts(producer.products_by_ids(brand, json.loads(row["product_ids"] or "[]")))


REDO_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in ("idea", "hook", "tagline", "cta", "direction", "sticker")},
    "required": ["idea", "hook", "tagline", "cta", "direction", "sticker"], "additionalProperties": False,
}


def redo(brand: Brand, plan_id: int, note: str = "", client=None) -> dict:
    """The Planner rewrites one calendar item, following your note."""
    from app.team import working
    row = _row(brand, plan_id) or _missing()
    if row["status"] != "planned":
        raise CalendarError("This item already has a draft — use 🔁 Regenerate on the draft instead.")
    notes = json.loads(row["notes"] or "{}")
    facts, prices, packs = _facts(brand, row)
    client = client or claude.client_for(brand)
    with working(brand, "planner", "Redoing a calendar item") as job:
        current = {k: (row[k] if k in ("idea", "hook") else notes.get(k, "")) for k in REDO_SCHEMA["properties"]}
        user = (f"Rewrite this {kind(row['format']).lower()} for {datetime.fromisoformat(row['scheduled_for']):%a %d %b}"
                f" ({planner.FORMAT_LABELS.get(row['format'], row['format'])}, pillar: {notes.get('pillar', '')}, "
                f"goal: {notes.get('goal', '')}).\nCurrent version: {json.dumps(current, ensure_ascii=False)}\n"
                f"Product facts (the only facts you may use):\n{facts or 'no product'}\n"
                f"The owner asks: {note.strip() or 'a fresh, stronger version'}\n"
                "Keep sticker empty unless this is a Story with a poll/quiz/question/link.")
        data, cost, _ = claude.ask(client, planner.system_prompt(brand), [{"role": "user", "content": user}],
                                   REDO_SCHEMA, effort="medium", max_tokens=4000)
        row["idea"], row["hook"] = data["idea"].strip() or row["idea"], data["hook"].strip() or row["hook"]
        for k in ("tagline", "cta", "direction", "sticker"):
            notes[k] = data[k].strip()
        notes["warnings"] = factcheck.check_text(f"{row['idea']} {row['hook']} {notes['tagline']}", brand, prices,
                                                 packs)
        notes.pop("idea_ok", None)
        _write(plan_id, row, notes)
        job["result"], job["cost_usd"] = row["hook"][:120], cost
    return {"cost_usd": cost, "hook": row["hook"]}


def _missing():
    raise CalendarError("That calendar item no longer exists.")


def week_context(brand: Brand, start: date, days: int = 7) -> str:
    """What the month's calendar already has for the coming days, so the weekly meeting adjusts it, not starts over."""
    existing = [it for it in items(brand, start, start + timedelta(days=days - 1)) if it["status"] == "planned"]
    if not existing:
        return ""
    lines = [f"- {it['when']:%a %d %b %I:%M %p} {it['type']}: {it['idea']} (hook: {it['hook']}; pillar "
             f"{it.get('pillar') or '?'}{'; the owner approved this idea' if it.get('idea_ok') else ''}"
             f"{'; edited by the owner' if json.loads(it['notes'] or '{}').get('edited_by_owner') else ''})"
             for it in existing]
    return ("\nTHE MONTH'S CALENDAR ALREADY HAS THESE FOR THESE DAYS — adjust rather than start over: keep items the "
            "owner approved or edited (same idea and hook), keep good ones, change only what results or the "
            "direction say should change:\n" + "\n".join(lines) + "\n")
