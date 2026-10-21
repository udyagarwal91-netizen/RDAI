"""Checks that text the AI wrote matches Shopify — before you ever see it.

Used on plan ideas, captions, on-image headlines and carousel slides.
Returns problems in plain English; an empty list means the text is fine.
"""

import re

from app.brands import Brand

_PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_PACK_RE = re.compile(r"\b(\d+)\s*-?\s*(?:pcs?|pieces?|pk)\b|pack\s+of\s+(\d+)|\b(\d+)\s*-?\s*pack\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b\d{1,2}\s*%")


def check_text(text: str, brand: Brand, allowed_prices: set[float], pack_sizes: set[int] | None = None) -> list[str]:
    problems = []
    for m in _PRICE_RE.finditer(text):
        value = float(m.group(1).replace(",", ""))
        if value not in allowed_prices:
            problems.append(f"mentions ₹{value:g}, which isn't a Shopify price for this product")
    if pack_sizes:
        for m in _PACK_RE.finditer(text):
            n = int(next(g for g in m.groups() if g))
            if n not in pack_sizes:
                problems.append(f"says {n}-piece pack, but Shopify says {'/'.join(map(str, sorted(pack_sizes)))}-piece")
    if _PERCENT_RE.search(text):
        problems.append("mentions a % discount — check it's real before posting")
    lower = text.lower()
    for word in brand.config.get("banned_words") or []:
        if re.search(rf"\b{re.escape(str(word).lower())}\b", lower):
            problems.append(f"uses the banned word '{word}'")
    for word in brand.config.get("needs_proof_words") or []:
        if re.search(rf"\b{re.escape(str(word).lower())}\b", lower):
            problems.append(f"says '{word}' — only OK if Shopify really shows it")
    return list(dict.fromkeys(problems))  # each problem once
