"""Saving brand settings from the Control Center.

The app edits brands/<brand>/config.yaml for you, keeping the notes (# lines)
in the file. Every save is checked; if the result would be broken, the old
file is put back and you see a plain-English message instead.
"""

import re
import threading
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from app import BRANDS_DIR
from app.brands import BrandConfigError, load_brand

_lock = threading.Lock()
LOGO_TYPES = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp"}
MAX_LOGO_BYTES = 5 * 1024 * 1024


def _yaml() -> YAML:
    y = YAML()  # round-trip mode: keeps comments and order
    y.preserve_quotes = True
    y.width = 120
    y.indent(mapping=2, sequence=4, offset=2)  # same layout as the hand-written file
    return y


def update_config(slug: str, changes: dict[str, object]) -> None:
    """Apply changes like {"meta.facebook_page_id": "123"} to config.yaml.

    Raises BrandConfigError (with a readable message) if the result is invalid;
    the file is left exactly as it was in that case.
    """
    path = BRANDS_DIR / slug / "config.yaml"
    with _lock:
        original = path.read_text(encoding="utf-8")
        data = _yaml().load(original)
        for dotted, value in changes.items():
            node = data
            *parents, last = dotted.split(".")
            for part in parents:
                if node.get(part) is None:
                    node[part] = {}
                node = node[part]
            node[last] = _quoted(value)
        out = StringIO()
        _yaml().dump(data, out)
        path.write_text(out.getvalue(), encoding="utf-8")
        try:
            load_brand(slug)
        except BrandConfigError:
            path.write_text(original, encoding="utf-8")
            raise


def _quoted(value):
    """Write text in "quotes" so values like 19:30 or 1234567 stay text, not numbers."""
    if isinstance(value, str):
        return DoubleQuotedScalarString(value)
    if isinstance(value, list):
        return [_quoted(v) for v in value]
    if isinstance(value, dict):
        return {k: _quoted(v) for k, v in value.items()}
    return value


def save_rules(slug: str, text: str) -> None:
    text = text.replace("\r\n", "\n").strip() + "\n"
    if len(text) < 50:
        raise BrandConfigError("The brand rules look empty. Write at least a few lines.")
    (BRANDS_DIR / slug / "brand-rules.md").write_text(text, encoding="utf-8")


def save_logo(slug: str, data: bytes, white_version: bool = False) -> str:
    """Store an uploaded logo and point config.yaml at it. Returns the saved path."""
    if len(data) > MAX_LOGO_BYTES:
        raise BrandConfigError("That logo file is bigger than 5 MB. Please use a smaller PNG.")
    ext = next((e for magic, e in LOGO_TYPES.items() if data.startswith(magic)), None)
    if ext is None:
        raise BrandConfigError("Please upload a PNG, JPG or WebP image.")
    name = ("logo-white" if white_version else "logo") + ext
    assets = BRANDS_DIR / slug / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for old in assets.glob(("logo-white" if white_version else "logo") + ".*"):
        if old.suffix in (".png", ".jpg", ".webp"):
            old.unlink()
    (assets / name).write_bytes(data)
    update_config(slug, {"style.logo_on_dark_path" if white_version else "style.logo_path": f"assets/{name}"})
    return f"assets/{name}"


def logo_file(slug: str, white_version: bool = False) -> Path | None:
    brand = load_brand(slug)
    rel = brand.config["style"].get("logo_on_dark_path" if white_version else "logo_path")
    if not rel:
        return None
    path = (brand.folder / rel).resolve()
    if brand.folder.resolve() not in path.parents or not path.is_file():
        return None
    return path


def normalize_shop_domain(raw: str) -> str:
    """Accept whatever the owner pastes and turn it into xyz.myshopify.com.

    Works with: xyz.myshopify.com, https://xyz.myshopify.com/admin,
    https://admin.shopify.com/store/xyz/products, or just xyz.
    """
    text = raw.strip().lower()
    m = re.search(r"admin\.shopify\.com/store/([a-z0-9][a-z0-9-]*)", text)
    if m:
        return f"{m.group(1)}.myshopify.com"
    text = re.sub(r"^https?://", "", text).split("/")[0]
    if text.endswith(".myshopify.com"):
        handle = text[: -len(".myshopify.com")]
    elif "." not in text:
        handle = text
    else:
        raise BrandConfigError(
            f"'{raw.strip()}' looks like your public website. I need the Shopify address instead: "
            "open your Shopify admin and copy the web address from the browser bar "
            "(it looks like admin.shopify.com/store/your-store-name).")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", handle):
        raise BrandConfigError("That doesn't look like a Shopify store address. Copy it from your browser bar.")
    return f"{handle}.myshopify.com"


def split_list(text: str) -> list[str]:
    """'a, b\\nc' → ['a', 'b', 'c'] (commas or new lines, blanks removed)."""
    return [part.strip() for part in re.split(r"[,\n]", text or "") if part.strip()]
