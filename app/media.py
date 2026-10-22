"""Image files: downloading product photos, saving drafts, making Instagram-ready JPEGs.

Everything lives under data/media/<brand>/ so it survives updates and is
never sent to GitHub.
"""

import hashlib
import io
import logging
from pathlib import Path

import requests
from PIL import Image, ImageOps

from app import DATA_DIR

log = logging.getLogger(__name__)
FEED_SIZE = (1080, 1350)  # 4:5 portrait — the size Instagram shows biggest in the feed
STORY_SIZE = (1080, 1920)


def media_dir(brand_slug: str, *parts: str) -> Path:
    path = DATA_DIR / "media" / brand_slug
    for p in parts:
        path = path / p
    path.mkdir(parents=True, exist_ok=True)
    return path


def sized_url(url: str, width: int) -> str:
    """Ask Shopify's image server for a smaller copy (faster, cheaper for AI)."""
    return f"{url}{'&' if '?' in url else '?'}width={width}"


def download(url: str, width: int = 1024) -> bytes:
    """Fetch a product photo (cached on disk so we don't download it again)."""
    cache = media_dir("_cache") / (hashlib.sha1(f"{url}|{width}".encode()).hexdigest() + ".img")
    if cache.exists():
        return cache.read_bytes()
    r = requests.get(sized_url(url, width), timeout=30)
    r.raise_for_status()
    cache.write_bytes(r.content)
    return r.content


def to_jpeg(data: bytes, size: tuple[int, int] = FEED_SIZE, logo: Path | None = None) -> bytes:
    """Crop/resize any image to `size` and return a JPEG (Instagram only accepts JPEG photos).
    Optionally stamps the brand logo small in the bottom-right corner."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, size, Image.Resampling.LANCZOS)
    if logo and logo.exists():
        mark = Image.open(logo).convert("RGBA")
        mark.thumbnail((size[0] // 6, size[1] // 12), Image.Resampling.LANCZOS)
        margin = size[0] // 30
        img.paste(mark, (size[0] - mark.width - margin, size[1] - mark.height - margin), mark)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue()


def thumbnail_jpeg(data: bytes, max_side: int = 768) -> bytes:
    """A smaller JPEG for the AI checker (cheaper to look at, same detail for checking)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()
