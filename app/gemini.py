"""AI images from Google Gemini (model and prices checked against Google's docs, Oct 2026).

The real Shopify product photo is always sent as the reference image, so the
product's colour, print, waistband and logo stay accurate. Text and logos
are never asked from the AI — the agent adds those itself.
"""

import base64
import logging

import requests

from app.brands import Brand

log = logging.getLogger(__name__)
API = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_MODEL = "gemini-nano-banana-2.1"
# US$ per output image (paid tier, standard), from ai.google.dev/gemini-api/docs/pricing
PRICE_PER_IMAGE = {"gemini-nano-banana-2.1": {"1K": 0.0336, "2K": 0.0504, "4K": 0.113},
                   "gemini-3.1-flash-image": {"1K": 0.067, "2K": 0.101, "4K": 0.151},
                   "gemini-3-pro-image": {"1K": 0.134, "2K": 0.134, "4K": 0.24}}
STYLE_RULES = {
    "product_only": "No people at all. Show only the product (and simple props), e.g. a flat-lay, folded packs, "
                    "a neat gym bag or travel kit, or a clean shelf.",
    "dressed_model": "One adult person in everyday clothes (trousers or shorts and a top), in a natural everyday "
                     "moment. The innerwear itself is shown as the pack beside them or a small neat waistband "
                     "detail — never worn on its own.",
    "website_model": "One adult male model wearing the product, styled like a premium e-commerce catalogue photo: "
                     "confident, relaxed standing pose, neutral or home-morning-routine setting, soft natural light. "
                     "Not suggestive: no bedroom or bed, no intimacy, no lingering crop on the body, no oil or water.",
}
COMMON_RULES = ("Use the attached product photo as the exact reference for the product: keep its colour, print, "
                "waistband, fabric and logo identical. Photorealistic, premium, clean composition, natural "
                "lighting. Absolutely no text, words, letters, prices or logos added to the image (they're "
                "added later). Correct human anatomy if any person appears. Tasteful and family-friendly.")


class GeminiError(Exception):
    pass


def generate_image(brand: Brand, scene: str, style: str, references: list[bytes], aspect: str = "4:5",
                   extra_instructions: str = "") -> tuple[bytes, float]:
    """Make one image. Returns (image bytes, cost in US$)."""
    key = brand.secret("GEMINI_API_KEY")
    if not key:
        raise GeminiError("Gemini isn't connected yet. Go to Connect accounts → Gemini.")
    cfg = brand.config.get("ai_images") or {}
    model = cfg.get("model", DEFAULT_MODEL)
    size = cfg.get("image_size", "2K")
    prompt = (f"Create a social media photo for the brand {brand.name}. Scene: {scene}\n\n"
              f"{STYLE_RULES.get(style, STYLE_RULES['product_only'])}\n\n{COMMON_RULES}")
    if extra_instructions:
        prompt += f"\n\nFix these problems from the previous attempt: {extra_instructions}"
    body = {
        "model": model,
        "input": [{"type": "text", "text": prompt}] + [
            {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(r).decode()} for r in references],
        "response_format": {"type": "image", "aspect_ratio": aspect, "image_size": size},
    }
    try:
        r = requests.post(API, json=body, headers={"x-goog-api-key": key}, timeout=180)
    except requests.RequestException:
        raise GeminiError("Couldn't reach Gemini. Check your internet connection.") from None
    if r.status_code in (401, 403):
        raise GeminiError("Gemini refused the key, or billing isn't on. Check Connect accounts → Gemini.")
    if r.status_code == 429:
        raise GeminiError("Gemini's limit was reached for now. Try again in a few minutes (or check billing).")
    payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        msg = (payload.get("error") or {}).get("message", "")[:200]
        raise GeminiError(f"Gemini replied with error {r.status_code}. {msg}".strip())
    # The answer can repeat our own input (with the reference photo), so look only at the model's output.
    steps = payload.get("steps")
    outputs = [s for s in steps if s.get("type") == "model_output"] if isinstance(steps, list) else [payload]
    image = _find_image(outputs)
    if image is None:
        reason = payload.get("status") or "no image returned"
        raise GeminiError(f"Gemini didn't make an image ({reason}) — often its safety filter. Try a different style.")
    cost = PRICE_PER_IMAGE.get(model, {}).get(size, 0.05)
    log.info("Gemini image made with %s (%s, %s), about US$%.3f", model, size, style, cost)
    return image, cost


def _find_image(node) -> bytes | None:
    """Find the first image in Gemini's answer, wherever it sits (tolerates format changes)."""
    if isinstance(node, dict):
        if node.get("type") == "image" and node.get("data"):
            return base64.b64decode(node["data"])
        inline = node.get("inline_data") or node.get("inlineData")
        if isinstance(inline, dict) and inline.get("data"):
            return base64.b64decode(inline["data"])
        children = node.values()
    elif isinstance(node, list):
        children = node
    else:
        return None
    for child in children:
        found = _find_image(child)
        if found:
            return found
    return None
