"""Loading brand settings from brands/<brand_name>/.

Everything brand-specific (store, accounts, colours, tone, rules) lives in
that folder. The code never hardcodes a brand. Adding a client brand later
means copying the folder and editing its files.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from app import BRANDS_DIR, secrets_store

REQUIRED_SECTIONS = ["brand", "shopify", "meta", "schedule", "content_mix", "style", "voice"]


class BrandConfigError(Exception):
    pass


@dataclass
class Brand:
    slug: str            # folder name, e.g. "skipperpacks"
    folder: Path
    config: dict
    rules: str           # contents of brand-rules.md

    @property
    def name(self) -> str:
        return self.config["brand"]["name"]

    @property
    def auto_publish(self) -> bool:
        return bool(self.config.get("auto_publish", False))

    def secret(self, key_name: str) -> str | None:
        """Get an API key for this brand.

        The first brand uses plain names (META_ACCESS_TOKEN). A client brand
        sets env_prefix in its config (e.g. "ACME_") and its keys are then
        ACME_META_ACCESS_TOKEN, so brands never share accounts by accident.
        Gemini/Claude keys are shared unless the brand sets its own.
        """
        prefix = self.config.get("env_prefix", "") or ""
        return secrets_store.get(prefix + key_name) or (
            secrets_store.get(key_name) if key_name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY") else None
        )


def list_brands() -> list[str]:
    if not BRANDS_DIR.exists():
        return []
    return sorted(p.name for p in BRANDS_DIR.iterdir() if (p / "config.yaml").exists())


def load_brand(slug: str) -> Brand:
    folder = BRANDS_DIR / slug
    config_path = folder / "config.yaml"
    if not config_path.exists():
        raise BrandConfigError(f"No brand called '{slug}'. Expected a file at {config_path}")
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise BrandConfigError(f"brands/{slug}/config.yaml has a typing mistake: {e}") from e

    missing = [s for s in REQUIRED_SECTIONS if s not in config]
    if missing:
        raise BrandConfigError(f"brands/{slug}/config.yaml is missing: {', '.join(missing)}")

    mix = config["content_mix"]
    if sum(mix.get("themes", {}).values()) != 100:
        raise BrandConfigError(f"brands/{slug}/config.yaml: content_mix.themes must add up to 100")
    if sum(mix.get("formats", {}).values()) != 100:
        raise BrandConfigError(f"brands/{slug}/config.yaml: content_mix.formats must add up to 100")

    rules_path = folder / "brand-rules.md"
    rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
    return Brand(slug=slug, folder=folder, config=config, rules=rules)
