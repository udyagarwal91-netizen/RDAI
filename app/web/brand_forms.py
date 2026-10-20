"""Turns each Brand-page form into config.yaml changes.

One function per section of the Brand page. Each returns a dict like
{"schedule.slots": [...]} for brand_editor.update_config, or raises
BrandConfigError with a message the owner can act on.
"""

import re

from app.brand_editor import normalize_shop_domain, split_list
from app.brands import BrandConfigError

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = dict(zip(DAYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]))
THEMES = ["product", "offer", "education", "lifestyle"]
FORMATS = ["template_image", "carousel", "gemini_image", "gemini_reel"]
COLORS = ["primary", "secondary", "accent", "text_dark", "text_light"]
HASHTAG_SETS = ["product", "education", "lifestyle", "festive"]
LANGUAGES = ["English", "English with light Hinglish", "Hinglish"]
EMOJI_LEVELS = ["none", "low", "medium"]


def _int(form, name: str, label: str, lo: int, hi: int) -> int:
    try:
        value = int(form.get(name, "").strip())
    except ValueError:
        raise BrandConfigError(f"{label}: please type a whole number.") from None
    if not lo <= value <= hi:
        raise BrandConfigError(f"{label}: must be between {lo} and {hi}.")
    return value


def _text(form, name: str, label: str, required: bool = True, max_len: int = 2000) -> str:
    value = (form.get(name) or "").strip()
    if required and not value:
        raise BrandConfigError(f"{label} can't be empty.")
    if len(value) > max_len:
        raise BrandConfigError(f"{label} is too long (max {max_len} characters).")
    return value


def _digits(form, name: str, label: str) -> str:
    value = re.sub(r"\s", "", form.get(name) or "")
    if value and not value.isdigit():
        raise BrandConfigError(f"{label} should be numbers only.")
    return value


def basics(form) -> dict:
    changes = {
        "brand.name": _text(form, "name", "Brand name", max_len=80),
        "brand.website": _text(form, "website", "Website", required=False, max_len=200),
        "brand.tagline": _text(form, "tagline", "Tagline", required=False, max_len=200),
        "meta.facebook_page_id": _digits(form, "facebook_page_id", "Facebook Page ID"),
        "meta.instagram_account_id": _digits(form, "instagram_account_id", "Instagram account ID"),
    }
    store = (form.get("store_domain") or "").strip()
    if store:
        changes["shopify.store_domain"] = normalize_shop_domain(store)
    return changes


def schedule(form) -> dict:
    slots = []
    for day in DAYS:
        if form.get(f"day_{day}"):
            time = (form.get(f"time_{day}") or "").strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
                raise BrandConfigError(f"Pick a posting time for {DAY_NAMES[day]}.")
            slots.append({"day": day, "time": time})
    if not slots:
        raise BrandConfigError("Tick at least one posting day.")
    plan_day = form.get("plan_day", "sun")
    if plan_day not in DAYS:
        raise BrandConfigError("Pick a day for making the weekly plan.")
    plan_time = (form.get("plan_time") or "10:00").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", plan_time):
        raise BrandConfigError("Pick a time for making the weekly plan.")
    horizon = _int(form, "plan_horizon_days", "Plan length", 7, 14)
    return {
        "schedule.slots": slots,
        "schedule.posts_per_week": len(slots),
        "schedule.plan_day": plan_day,
        "schedule.plan_time": plan_time,
        "schedule.plan_horizon_days": horizon,
    }


def content_mix(form) -> dict:
    themes = {t: _int(form, f"theme_{t}", f"{t.title()} share", 0, 100) for t in THEMES}
    formats = {f: _int(form, f"format_{f}", "Format share", 0, 100) for f in FORMATS}
    if sum(themes.values()) != 100:
        raise BrandConfigError(f"Post topics add up to {sum(themes.values())}%. They must add up to exactly 100%.")
    if sum(formats.values()) != 100:
        raise BrandConfigError(f"Post formats add up to {sum(formats.values())}%. They must add up to exactly 100%.")
    return {
        "content_mix.themes": themes,
        "content_mix.formats": formats,
        "content_mix.max_reels_per_week": _int(form, "max_reels_per_week", "Reels per week", 0, 14),
    }


def look(form) -> dict:
    changes = {}
    for name in COLORS + ["product_photo_backdrop"]:
        value = (form.get(f"color_{name}") or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise BrandConfigError("Colours must be picked with the colour boxes (like #0B2545).")
        key = "style.product_photo_backdrop" if name == "product_photo_backdrop" else f"style.colors.{name}"
        changes[key] = value.upper()
    changes["style.fonts.heading"] = _text(form, "font_heading", "Heading font", max_len=60)
    changes["style.fonts.body"] = _text(form, "font_body", "Body font", max_len=60)
    return changes


def _hashtags(text: str) -> list[str]:
    tags = []
    for raw in split_list(text.replace(" #", ",#")):
        tag = "#" + re.sub(r"[^\w]", "", raw.lstrip("#"))
        if len(tag) > 1 and tag not in tags:
            tags.append(tag)
    return tags


def voice(form) -> dict:
    language = form.get("language")
    if language not in LANGUAGES:
        raise BrandConfigError("Pick a caption language.")
    emoji = form.get("emoji_level")
    if emoji not in EMOJI_LEVELS:
        raise BrandConfigError("Pick an emoji level.")
    changes = {
        "voice.language": language,
        "voice.hinglish_share_percent": _int(form, "hinglish_share_percent", "Hinglish share", 0, 100),
        "voice.tone": _text(form, "tone", "Tone of voice"),
        "voice.caption_length": _text(form, "caption_length", "Caption length", max_len=200),
        "voice.emoji_level": emoji,
        "voice.call_to_action": _text(form, "call_to_action", "Call to action", max_len=200),
        "hashtags.always": _hashtags(form.get("hashtags_always", "")),
        "hashtags.max_per_post": _int(form, "hashtags_max", "Hashtags per post", 0, 30),
    }
    for name in HASHTAG_SETS:
        changes[f"hashtags.sets.{name}"] = _hashtags(form.get(f"hashtags_{name}", ""))
    return changes


def safety(form) -> dict:
    return {
        "banned_words": split_list(form.get("banned_words", "")),
        "needs_proof_words": split_list(form.get("needs_proof_words", "")),
    }


def approval(form) -> dict:
    return {"auto_publish": form.get("auto_publish") == "on"}


SECTIONS = {
    "basics": basics,
    "schedule": schedule,
    "mix": content_mix,
    "look": look,
    "voice": voice,
    "safety": safety,
    "approval": approval,
}
