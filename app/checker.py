"""The final quality check, done by Claude looking at the actual pictures.

It compares what was made with the real product photo and flags: wrong
colour / print / waistband / logo, distorted bodies or extra limbs, garbled
text, anything suggestive, and captions that break the brand rules.
"""

import base64
import logging

from app import claude, media
from app.brands import Brand

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["passed", "problems"],
    "additionalProperties": False,
}


def system_prompt(brand: Brand) -> str:
    return f"""You are the strict final quality checker for {brand.name}'s social media posts (innerwear brand).
Look at the post images and caption and list every real problem, in short plain English an owner understands.

Check:
1. Product accuracy (AI-made images only): compared with the reference product photo, is the product's colour,
   print, waistband design and logo the same? Is the product clearly recognisable?
2. Picture quality: distorted or extra fingers/limbs, odd faces or bodies, melted objects, garbled or misspelled text,
   watermarks, cut-off product.
3. Content safety: nothing suggestive or sexualised (no bedroom/intimate setting, no provocative pose, no lingering
   crop on the body), no body-shaming. Catalogue-style product modelling is acceptable if it is neutral and tasteful.
4. Caption: matches the brand rules, no invented offers, natural English (light Hinglish is fine).

Set "passed" to false if there is any problem the owner must fix before posting. Minor taste notes don't fail a post.
Brand rules for reference:
<brand_rules>
{brand.rules.strip()[:4000]}
</brand_rules>"""


def check(brand: Brand, images: list[bytes], reference_url: str | None, caption: str, ai_made: bool,
          client=None) -> tuple[bool, list[str], float]:
    """Returns (passed, problems, cost in US$)."""
    client = client or claude.client_for(brand)
    content = []
    if ai_made and reference_url:
        content += [{"type": "text", "text": "Reference: the real product photo from the store."},
                    {"type": "image", "source": {"type": "url", "url": media.sized_url(reference_url, 600)}}]
    kind = "AI-made image" if ai_made else "designed from the real product photo (product accuracy is not a concern)"
    for i, img in enumerate(images, 1):
        content += [{"type": "text", "text": f"Post image {i} of {len(images)} ({kind}):"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(media.thumbnail_jpeg(img)).decode()}}]
    content.append({"type": "text", "text": f"Caption:\n{caption}"})
    answer, cost, _ = claude.ask(client, system_prompt(brand), [{"role": "user", "content": content}], SCHEMA,
                                 effort="medium", max_tokens=8000)
    passed = bool(answer["passed"])
    problems = [] if passed else [p.strip() for p in answer["problems"] if p.strip()] or ["the checker wasn't happy"]
    log.info("Quality check: %s%s", "passed" if passed else "FLAGGED", f" — {'; '.join(problems)}" if problems else "")
    return passed, problems, cost
