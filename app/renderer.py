"""Turns HTML design templates into post images (JPEG), using a hidden browser.

Templates live in app/default_templates/. A brand can replace any of them by
putting a file with the same name in brands/<brand>/templates/.
"""

import base64
import logging
import mimetypes
import os
import subprocess
import sys
import threading
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from app.brands import Brand

log = logging.getLogger(__name__)
DEFAULT_TEMPLATES = Path(__file__).parent / "default_templates"
_browser_ready = threading.Event()
_browser_problem: str | None = None


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- one-time browser install

def ensure_browser() -> None:
    """Download the hidden browser the first time (about 150 MB). Runs in the background at start-up."""
    global _browser_problem
    if os.environ.get("CHROMIUM_PATH") or os.environ.get("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"):
        _browser_ready.set()
        return
    try:
        log.info("Checking the picture maker (first time: downloads about 150 MB, takes a few minutes)…")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True,
                       capture_output=True, timeout=1800)
        log.info("Picture maker ready.")
    except Exception as e:  # noqa: BLE001 — any failure becomes a readable message
        _browser_problem = f"Couldn't install the picture maker ({type(e).__name__}). Check your internet and restart."
        log.error(_browser_problem)
    finally:
        _browser_ready.set()


def start_browser_install() -> None:
    threading.Thread(target=ensure_browser, name="browser-install", daemon=True).start()


# ---------------------------------------------------------------- rendering

def data_uri(data: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def file_uri(path: Path) -> str | None:
    if not path or not path.exists():
        return None
    return data_uri(path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png")


def _env(brand: Brand) -> Environment:
    return Environment(
        loader=ChoiceLoader([FileSystemLoader(str(brand.folder / "templates")), FileSystemLoader(str(DEFAULT_TEMPLATES))]),
        autoescape=select_autoescape(["html"]),
    )


def brand_context(brand: Brand, width: int, height: int) -> dict:
    style = brand.config["style"]
    logo = (brand.folder / style["logo_path"]) if style.get("logo_path") else None
    website = (brand.config["brand"].get("website") or "").replace("https://", "").replace("http://", "").strip("/")
    return {
        "c": style["colors"], "fonts": style["fonts"], "backdrop": style.get("product_photo_backdrop", "#FFFFFF"),
        "logo": file_uri(logo) if logo else None, "brand_name": brand.name, "website": website,
        "width": width, "height": height,
    }


def render(brand: Brand, template: str, pages: list[dict], size: tuple[int, int] = (1080, 1350)) -> list[bytes]:
    """Render one JPEG per page dict (each dict fills the template's blanks)."""
    _browser_ready.wait(timeout=1800)
    if _browser_problem:
        raise RenderError(_browser_problem)
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    env = _env(brand)
    base = brand_context(brand, *size)
    images = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": size[0], "height": size[1]})
            for fields in pages:
                html = env.get_template(template).render(**base, **fields)
                page.set_content(html, wait_until="networkidle", timeout=30000)
                page.evaluate("document.fonts.ready")
                images.append(page.screenshot(type="jpeg", quality=92, full_page=False))
            browser.close()
    except PlaywrightError as e:
        raise RenderError(f"The picture maker failed: {str(e).splitlines()[0][:200]}") from None
    return images
