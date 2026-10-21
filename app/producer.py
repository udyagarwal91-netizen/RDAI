"""Makes a finished draft for one planned post.

Steps: write caption → make the picture(s) → quality check → if flagged, fix
and check once more → save → (the caller sends it to Telegram).
Prices on images always come from Shopify, never from the AI.
"""

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import checker, gemini, media, renderer, writer
from app.brands import Brand
from app.db import connect
from app.planner import FORMAT_LABELS, catalog_styles, price_text

log = logging.getLogger(__name__)


class DraftError(Exception):
    pass


def _now(brand: Brand) -> datetime:
    return datetime.now(ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata")))


def products_by_ids(brand: Brand, ids: list[str]) -> list[dict]:
    with connect() as conn:
        rows = {r["shopify_id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM products WHERE brand = ? AND shopify_id IN ({','.join('?' * len(ids))})",
            [brand.slug, *ids])} if ids else {}
    return [rows[i] for i in ids if i in rows]


def _badge(products: list[dict]) -> str:
    return f"{products[0]['pack_size']}-PC PACK" if products and products[0]["pack_size"] else ""


def _price_fields(products: list[dict]) -> dict:
    if not products:
        return {"price": "", "mrp": ""}
    p = products[0]
    return {"price": price_text(p), "mrp": f"₹{p['compare_at_min']:g}" if p["compare_at_min"] else ""}


def _short_cta(brand: Brand) -> str:
    return brand.config["style"].get("cta_short", "Shop the pack · link in bio")


def make_visuals(brand: Brand, fmt: str, creative: dict, products: list[dict], photos: list[str],
                 fix: str = "", owner_note: str = "") -> tuple[list[bytes], float, bool]:
    """Returns (JPEG images, AI image cost in US$, made_by_ai)."""
    if not photos:
        raise DraftError("This product has no photos in Shopify, so no picture can be made.")
    photo_url = photos[min(creative["photo_index"], len(photos) - 1)]
    photo = media.download(photo_url, 1024)
    if fmt == "template_image":
        page = {"photo": renderer.data_uri(photo), "headline": creative["headline"], "subline": creative["subline"],
                "badge": _badge(products), "cta": _short_cta(brand), **_price_fields(products)}
        return renderer.render(brand, "post.html", [page]), 0.0, False
    if fmt == "carousel":
        middle = creative["slides"][:3]
        total = len(middle) + 2
        pages = [{"kind": "cover", "photo": renderer.data_uri(photo), "headline": creative["headline"]}]
        pages += [{"kind": "info", "headline": s["headline"], "body": s["body"]} for s in middle]
        pages.append({"kind": "end", "photo": renderer.data_uri(photo),
                      "headline": f"{products[0]['title']}" if products else creative["headline"],
                      "body": creative["subline"], "cta": _short_cta(brand), **_price_fields(products)})
        for i, p in enumerate(pages, 1):
            p.update(index=i, total=total)
        return renderer.render(brand, "slide.html", pages), 0.0, False
    # gemini_image (and reels, until Phase 2b): AI picture from the real product photo
    image, cost = gemini.generate_image(brand, creative["scene"], creative["image_style"], [photo],
                                        extra_instructions="; ".join(filter(None, [
                                            f"The owner asked for: {owner_note}" if owner_note else "", fix])))
    logo_rel = brand.config["style"].get("logo_path")
    logo = brand.folder / logo_rel if logo_rel else None
    return [media.to_jpeg(image, media.FEED_SIZE, logo=logo)], cost, True


def make_draft(brand: Brand, plan_row: dict | None = None, *, on_demand: dict | None = None,
               replace_draft_id: int | None = None, owner_note: str = "", client=None) -> int:
    """Make (or remake) one draft. `owner_note` is what the owner typed for a regenerate. Returns the draft id."""
    post = dict(plan_row or on_demand)
    post["owner_note"] = owner_note.strip()
    product_ids = json.loads(post.get("product_ids") or "[]")
    products = products_by_ids(brand, product_ids)
    if not products:
        raise DraftError("The product for this post is no longer in the store (or out of stock). Make a new plan.")
    notes = []
    if post["format"] == "gemini_reel":
        notes.append("reels arrive in Phase 2b — made an AI image instead for now")
        post["format"] = "gemini_image"
    when = datetime.fromisoformat(post["scheduled_for"]) if post.get("scheduled_for") else None
    post["when_label"] = when.strftime("%a %d %b, %I:%M %p") if when else "not scheduled"

    # 1. Words — with one chance to fix anything that doesn't match Shopify.
    w = writer.write(brand, post, products, client=client)
    cost = w["cost_usd"]
    if w["warnings"]:
        log.info("Caption problems (%s); asking for a fix", "; ".join(w["warnings"]))
        w = writer.write(brand, post, products, client=client, feedback="; ".join(w["warnings"]))
        cost += w["cost_usd"]
    creative, caption, photos = w["creative"], w["caption"], w["photos"]
    warnings = list(w["warnings"])

    # 2. Pictures, 3. quality check, 4. one automatic fix if flagged.
    note = post.get("owner_note", "")
    images, image_cost, ai_made = make_visuals(brand, post["format"], creative, products, photos, owner_note=note)
    cost += image_cost
    ref = photos[min(creative["photo_index"], len(photos) - 1)]
    passed, problems, check_cost = checker.check(brand, images, ref, caption, ai_made, client=client)
    cost += check_cost
    attempts = 1
    if not passed:
        attempts = 2
        fix = "; ".join(problems)
        if ai_made:
            images, image_cost, _ = make_visuals(brand, post["format"], creative, products, photos, fix=fix,
                                                 owner_note=note)
            cost += image_cost
        else:
            w = writer.write(brand, post, products, client=client, feedback=fix)
            cost += w["cost_usd"]
            creative, caption = w["creative"], w["caption"]
            warnings = list(w["warnings"])
            images, _, _ = make_visuals(brand, post["format"], creative, products, photos, owner_note=note)
        passed, problems, check_cost = checker.check(brand, images, ref, caption, ai_made, client=client)
        cost += check_cost
        if not passed:
            warnings += [f"check carefully: {p}" for p in problems]
    warnings += notes

    return _save(brand, post, plan_row, creative, caption, images, warnings, cost, attempts, ai_made,
                 replace_draft_id)


def _save(brand, post, plan_row, creative, caption, images, warnings, cost, attempts, ai_made, replace_id) -> int:
    now = _now(brand).isoformat(timespec="seconds")
    row = {
        "brand": brand.slug, "plan_id": plan_row["id"] if plan_row else None, "format": post["format"],
        "caption": caption, "hashtags": json.dumps(creative["hashtags"]), "scheduled_for": post.get("scheduled_for"),
        "quality_flags": json.dumps(warnings), "cost_usd": round(cost, 4), "status": "pending",
        "creative": json.dumps(creative), "image_style": creative["image_style"] if ai_made else "template",
        "theme": post["theme"], "product_ids": post.get("product_ids"), "created_at": now,
    }
    with connect() as conn:
        if replace_id:
            old = conn.execute("SELECT attempt, cost_usd FROM drafts WHERE id = ?", (replace_id,)).fetchone()
            row["attempt"] = (old["attempt"] if old else 1) + 1
            row["cost_usd"] = round(row["cost_usd"] + (old["cost_usd"] if old else 0), 4)
            conn.execute(f"UPDATE drafts SET {', '.join(f'{k} = ?' for k in row)} WHERE id = ?",
                         [*row.values(), replace_id])
            draft_id = replace_id
        else:
            row["attempt"] = attempts
            cur = conn.execute(f"INSERT INTO drafts ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                               list(row.values()))
            draft_id = cur.lastrowid
        if plan_row:
            conn.execute("UPDATE content_plan SET status = 'drafted' WHERE id = ?", (plan_row["id"],))

    folder = media.media_dir(brand.slug, "drafts", str(draft_id))
    for old_file in folder.glob("*.jpg"):
        old_file.unlink()
    paths = []
    for i, img in enumerate(images, 1):
        path = folder / f"{i}.jpg"
        path.write_bytes(img)
        paths.append(str(path.relative_to(media.media_dir(brand.slug).parent.parent)))
    with connect() as conn:
        conn.execute("UPDATE drafts SET media_paths = ? WHERE id = ?", (json.dumps(paths), draft_id))
    if brand.auto_publish and not warnings:
        decide(brand, draft_id, "auto")
    log.info("Draft #%d saved (%s, %d image(s), cost about US$%.3f%s)", draft_id, post["format"], len(images), cost,
             f", {len(warnings)} warning(s)" if warnings else "")
    return draft_id


# ---------------------------------------------------------------- reading drafts / decisions

def get_draft(brand: Brand, draft_id: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM drafts WHERE brand = ? AND id = ?", (brand.slug, draft_id)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["warnings"] = json.loads(d["quality_flags"] or "[]")
    d["creative"] = json.loads(d["creative"] or "{}")
    d["files"] = [media.media_dir(brand.slug).parent.parent / p for p in json.loads(d["media_paths"] or "[]")]
    d["products"] = products_by_ids(brand, json.loads(d["product_ids"] or "[]"))
    d["when"] = datetime.fromisoformat(d["scheduled_for"]) if d["scheduled_for"] else None
    d["format_label"] = FORMAT_LABELS.get(d["format"], d["format"])
    return d


def recent_drafts(brand: Brand, limit: int = 30) -> list[dict]:
    with connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM drafts WHERE brand = ? ORDER BY id DESC LIMIT ?",
                                              (brand.slug, limit))]
    return [get_draft(brand, i) for i in ids]


def decide(brand: Brand, draft_id: int, decision: str, note: str | None = None) -> datetime | None:
    """Record approve / reject / edit_caption / regenerate / auto, and update the draft's status.
    Approving puts the draft in the posting queue; returns when it will go out."""
    status = {"approve": "approved", "auto": "approved", "reject": "rejected", "regenerate": "regenerating"}.get(decision)
    with connect() as conn:
        conn.execute("INSERT INTO approvals (brand, draft_id, decision, note, decided_at) VALUES (?, ?, ?, ?, ?)",
                     (brand.slug, draft_id, decision, note, _now(brand).isoformat(timespec="seconds")))
        if status:
            conn.execute("UPDATE drafts SET status = ? WHERE id = ? AND brand = ?", (status, draft_id, brand.slug))
        if decision == "edit_caption" and note is not None:
            conn.execute("UPDATE drafts SET caption = ? WHERE id = ? AND brand = ?", (note, draft_id, brand.slug))
    log.info("Draft #%d: %s", draft_id, decision)
    if status == "approved":
        from app import publisher
        return publisher.queue_draft(brand, draft_id)
    return None


def remake(brand: Brand, draft_id: int, note: str = "", client=None) -> int:
    d = get_draft(brand, draft_id)
    if not d:
        raise DraftError(f"Draft #{draft_id} doesn't exist.")
    plan_row = None
    if d["plan_id"]:
        with connect() as conn:
            r = conn.execute("SELECT * FROM content_plan WHERE id = ?", (d["plan_id"],)).fetchone()
        plan_row = dict(r) if r else None
        if plan_row:
            plan_row["format"] = d["format"]  # keep what this draft is (e.g. a reel slot made as an AI image)
    on_demand = None if plan_row else {"format": d["format"], "theme": d["theme"], "product_ids": d["product_ids"],
                                       "idea": "A fresh, different take on this product", "hook": ""}
    return make_draft(brand, plan_row, on_demand=on_demand, replace_draft_id=draft_id, owner_note=note, client=client)


# ---------------------------------------------------------------- which posts need drafts

def due_plan_rows(brand: Brand, days: int = 7, limit: int = 10) -> list[dict]:
    now = _now(brand)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM content_plan WHERE brand = ? AND status = 'planned' AND scheduled_for >= ? "
            "AND scheduled_for < ? ORDER BY scheduled_for LIMIT ?",
            (brand.slug, now.isoformat(), (now + timedelta(days=days)).isoformat(), limit)).fetchall()
    return [dict(r) for r in rows]


def on_demand_post(brand: Brand, style_id: str, note: str = "") -> dict:
    """A one-off AI image request for a product style (from /image or the Control Center)."""
    style = next((s for s in catalog_styles(brand) if s["id"] == style_id), None)
    if not style:
        raise DraftError("That product isn't available any more. Get products from Shopify and try again.")
    return {"format": "gemini_image", "theme": "lifestyle", "product_ids": json.dumps(style["product_ids"][:1]),
            "idea": note or f"A fresh lifestyle image of the {style['title']}", "hook": ""}


def find_style(brand: Brand, query: str) -> dict | None:
    """Match '/image active brief 3' to the closest product style by words in common."""
    words = {w for w in query.lower().split() if w}
    best, best_score = None, 0
    for s in catalog_styles(brand):
        title = s["title"].lower()
        score = len(words & (set(title.split()) | set(title.replace("pcs", "pc").split())))
        if score > best_score:
            best, best_score = s, score
    return best
