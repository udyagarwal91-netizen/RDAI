"""Writes the caption, hashtags, on-image words and picture brief for one post.

Claude looks at the planned idea, the real product facts from Shopify and the
product photos, then returns everything in a fixed shape. Every number it
writes is checked against Shopify; if something doesn't match it gets one
chance to fix it, otherwise the draft is marked ⚠️ for you.
"""

import json
import logging

from app import claude, factcheck, media
from app.brands import Brand
from app.gemini import STYLE_RULES

log = logging.getLogger(__name__)
ALL_IMAGE_STYLES = list(STYLE_RULES)  # product_only, dressed_model, website_model
MAX_PHOTOS = 4


def allowed_image_styles(brand: Brand) -> list[str]:
    styles = (brand.config.get("ai_images") or {}).get("allowed_styles") or ALL_IMAGE_STYLES
    return [s for s in styles if s in ALL_IMAGE_STYLES] or ["product_only"]


def product_facts(products: list[dict]) -> tuple[str, set[float], set[int]]:
    """Facts text for the prompt + the prices and pack sizes the text is allowed to mention."""
    lines, prices, packs = [], set(), set()
    for p in products:
        variants = [v for v in json.loads(p["variants"] or "[]") if v["price"] > 0]
        prices |= {v["price"] for v in variants} | {v["compare_at"] for v in variants if v.get("compare_at")}
        if p["pack_size"]:
            packs.add(p["pack_size"])
        by_size = ", ".join(f"{v['size']} ₹{v['price']:g}" + (f" (MRP ₹{v['compare_at']:g})" if v.get("compare_at") else "")
                            for v in variants)
        lines.append(f"- {p['title']} | {p['pack_size'] or '?'}-piece pack | prices by size: {by_size} | "
                     f"in stock: {p['total_stock']} | description: {(p['description'] or '')[:500]}")
    return "\n".join(lines), prices, packs


def system_prompt(brand: Brand) -> str:
    c = brand.config
    v = c["voice"]
    tags = c.get("hashtags") or {}
    return f"""You write Instagram and Facebook posts for {brand.name}.

Non-negotiable rules:
1. Facts only from the product data given: never invent prices, discounts, offers, "limited stock", free gifts,
   delivery promises, sizes, colours or pack sizes. Prices vary by size, so say "from ₹X" using the lowest price.
   Only mention an MRP if one is given. When unsure, leave the number out.
2. Innerwear must never be suggestive or sexualised: no innuendo, no body-shaming, no bedroom framing.
   Premium everyday-essentials tone a customer's parents could see.
3. No competitor names, no health or medical claims.
4. Never use: {", ".join(map(str, c.get("banned_words") or []))}.

Voice: {v.get("tone", "").strip()}
Language: {v.get("language")}. About {v.get("hinglish_share_percent", 0)}% of posts may open with a light Hinglish
hook; product facts always in clear English. Emojis: {v.get("emoji_level")}. Caption length: {v.get("caption_length")}.
End the caption with this call to action (reworded naturally is fine): {v.get("call_to_action")}

Hashtags: always include {", ".join(tags.get("always") or [])}. Pick the rest from these sets where they fit:
{json.dumps(tags.get("sets") or {})}. At most {tags.get("max_per_post", 12)} in total. Put hashtags ONLY in the
"hashtags" list, never inside the caption.

On-image words: "headline" max 6 words, "subline" max 8 words. No prices in them (prices are added from Shopify).

Picture brief (for AI images): "scene" describes the photo to create — setting, props, light, mood — in one or two
sentences, without any text or logos. Pick "image_style" to suit the post:
- product_only: flat-lays, folded packs, gym bag, travel kit (safest; good for product/offer/education)
- dressed_model: a person in everyday clothes with the pack nearby (good for lifestyle routines)
- website_model: a model wearing the product like a premium catalogue photo, never suggestive (use sparingly)
Pick "photo_index": the product photo that shows the product most clearly (prefer clean product shots).

Carousel posts need 2–3 "slides" (headline max 6 words + body max 25 words each) for the middle of the carousel —
the agent adds a cover slide and a final price slide itself. Other formats: return an empty slides list.

The owner's brand rules (follow them):
<brand_rules>
{brand.rules.strip()}
</brand_rules>"""


def schema(n_photos: int, styles: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "headline": {"type": "string"},
            "subline": {"type": "string"},
            "photo_index": {"type": "integer", "enum": list(range(max(n_photos, 1)))},
            "image_style": {"type": "string", "enum": styles},
            "scene": {"type": "string"},
            "slides": {"type": "array", "items": {
                "type": "object",
                "properties": {"headline": {"type": "string"}, "body": {"type": "string"}},
                "required": ["headline", "body"], "additionalProperties": False}},
        },
        "required": ["caption", "hashtags", "headline", "subline", "photo_index", "image_style", "scene", "slides"],
        "additionalProperties": False,
    }


def write(brand: Brand, post: dict, products: list[dict], client=None, feedback: str = "") -> dict:
    """Returns {"creative": {...}, "caption": full text, "warnings": [...], "cost_usd": float}."""
    client = client or claude.client_for(brand)
    facts, prices, packs = product_facts(products)
    photos = json.loads(products[0]["images"] or "[]")[:MAX_PHOTOS] if products else []
    styles = allowed_image_styles(brand)

    content = [{"type": "text", "text": f"""Write this post.

When: {post.get('when_label', 'not scheduled')}
Format: {post['format']}   Topic: {post['theme']}
Planned idea: {post.get('idea', '')}
Suggested opening line: {post.get('hook', '')}

Product facts (from the store — the only facts you may use):
{facts}

The product photos follow, numbered from 0."""}]
    for i, url in enumerate(photos):
        content += [{"type": "text", "text": f"Photo {i}:"},
                    {"type": "image", "source": {"type": "url", "url": media.sized_url(url, 600)}}]
    if feedback:
        content.append({"type": "text", "text": f"Your previous version had problems — fix them: {feedback}"})

    answer, cost, _ = claude.ask(client, system_prompt(brand), [{"role": "user", "content": content}],
                                 schema(len(photos), styles), effort="high")
    answer["hashtags"] = _clean_hashtags(brand, answer["hashtags"])
    text = " ".join([answer["caption"], answer["headline"], answer["subline"]]
                    + [f"{s['headline']} {s['body']}" for s in answer["slides"]])
    warnings = factcheck.check_text(text, brand, prices, packs)
    if post["format"] == "carousel" and not answer["slides"]:
        warnings.append("the carousel has no middle slides")
    return {"creative": answer, "caption": full_caption(answer), "warnings": warnings, "cost_usd": cost,
            "photos": photos}


def full_caption(creative: dict) -> str:
    return creative["caption"].strip() + ("\n\n" + " ".join(creative["hashtags"]) if creative["hashtags"] else "")


def _clean_hashtags(brand: Brand, tags: list[str]) -> list[str]:
    cfg = brand.config.get("hashtags") or {}
    out = []
    for t in [*(cfg.get("always") or []), *tags]:
        t = "#" + "".join(ch for ch in str(t).lstrip("#") if ch.isalnum() or ch == "_")
        if len(t) > 1 and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out[: int(cfg.get("max_per_post", 12))]
