"""Reading products from Shopify (read-only — this never changes your store).

sync_products() copies every product, its sizes, prices, stock and photos
into the agent's database. Everything the agent later says about price or
pack size comes from these rows, never from AI.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from app.brands import Brand
from app.db import connect, set_state

log = logging.getLogger(__name__)
TIMEOUT = 30
PAGE_SIZE = 10  # products per request; keeps each request under Shopify's query-cost limit

_token_cache: dict[str, tuple[str, float]] = {}  # brand → (token, expires_at)

PRODUCTS_QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id handle title description productType tags status createdAt totalInventory onlineStoreUrl
      priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount } }
      collections(first: 10) { nodes { title } }
      featuredMedia { ... on MediaImage { image { url } } }
      media(first: 8) { nodes { ... on MediaImage { image { url } } } }
      variants(first: 50) {
        nodes { title sku price compareAtPrice inventoryQuantity availableForSale selectedOptions { name value } }
      }
    }
  }
}
"""


class ShopifyError(Exception):
    """A problem talking to Shopify, with a message the owner can act on."""


@dataclass
class SyncResult:
    total: int
    new: int
    removed: int
    in_stock: int
    problems: int = 0  # products with data mistakes (e.g. a ₹0 price)


def access_token(brand: Brand) -> str:
    """A working Admin API token: the old permanent shpat_ one, or a fresh
    24-hour one swapped for the Client ID + secret (cached until it expires)."""
    legacy = brand.secret("SHOPIFY_ADMIN_TOKEN")
    if legacy:
        return legacy
    cached = _token_cache.get(brand.slug)
    if cached and cached[1] > time.time() + 300:
        return cached[0]

    client_id = brand.secret("SHOPIFY_CLIENT_ID")
    client_secret = brand.secret("SHOPIFY_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise ShopifyError("Shopify isn't connected yet. Go to Connect accounts → Shopify.")
    r = requests.post(
        f"https://{store_domain(brand)}/admin/oauth/access_token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=TIMEOUT,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code == 200 and body.get("access_token"):
        _token_cache[brand.slug] = (body["access_token"], time.time() + int(body.get("expires_in", 86399)))
        return body["access_token"]
    if "shop_not_permitted" in r.text:
        raise ShopifyError("Shopify refused: the app and the store are not in the same Shopify organization. "
                           "See Connect accounts → Shopify.")
    if r.status_code in (400, 401):
        raise ShopifyError("Shopify rejected the Client ID / secret. Check both on Connect accounts → Shopify, "
                           "and that the app is installed on your store.")
    raise ShopifyError(f"Shopify replied with error {r.status_code}. Is the app installed on your store?")


def store_domain(brand: Brand) -> str:
    domain = brand.config["shopify"].get("store_domain") or ""
    if not domain or "your-store" in domain:
        raise ShopifyError("Add your Shopify store address first (Connect accounts → Shopify).")
    return domain


def graphql(brand: Brand, query: str, variables: dict | None = None) -> dict:
    """Run one Admin GraphQL query. Waits and retries if Shopify says "slow down"."""
    version = brand.config["shopify"].get("api_version", "2026-10")
    url = f"https://{store_domain(brand)}/admin/api/{version}/graphql.json"
    for attempt in range(5):
        r = requests.post(url, json={"query": query, "variables": variables or {}},
                          headers={"X-Shopify-Access-Token": access_token(brand)}, timeout=TIMEOUT)
        if r.status_code == 401:
            _token_cache.pop(brand.slug, None)  # token expired early; get a new one next time
            raise ShopifyError("Shopify said the access key is no longer valid. Press Save & test on "
                               "Connect accounts → Shopify.")
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise ShopifyError(f"Shopify replied with error {r.status_code}.")
        payload = r.json()
        errors = payload.get("errors") or []
        if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
            time.sleep(2 * (attempt + 1))
            continue
        if errors:
            text = "; ".join(e.get("message", "") for e in errors)[:300]
            if "access" in text.lower() or "scope" in text.lower():
                raise ShopifyError("The Shopify app is missing a permission. In the Dev Dashboard, tick "
                                   "read_products and read_inventory, release a new version and reinstall.")
            raise ShopifyError(f"Shopify couldn't answer: {text}")
        return payload["data"]
    raise ShopifyError("Shopify is busy right now. Try again in a few minutes.")


_PACK_RE = re.compile(r"(\d+)\s*-?\s*(?:pcs?|pieces?|pk)\b|pack\s+of\s+(\d+)", re.IGNORECASE)


def pack_size(title: str, tags: list[str]) -> int | None:
    """Pieces per pack, read from the title first, then the tags. None if unclear."""
    for text in [title, *tags]:
        m = _PACK_RE.search(text or "")
        if m:
            return int(m.group(1) or m.group(2))
    return None


def _row(node: dict) -> dict:
    variants = []
    for v in node["variants"]["nodes"]:
        variants.append({
            "size": next((o["value"] for o in v["selectedOptions"] if o["name"].lower() == "size"), v["title"]),
            "options": {o["name"]: o["value"] for o in v["selectedOptions"]},
            "price": float(v["price"]),
            "compare_at": float(v["compareAtPrice"]) if v.get("compareAtPrice") else None,
            "sku": v.get("sku"),
            "stock": v.get("inventoryQuantity"),
            "available": v.get("availableForSale", True),
        })
    images = []
    featured = ((node.get("featuredMedia") or {}).get("image") or {}).get("url")
    if featured:
        images.append(featured)
    for m in node["media"]["nodes"]:
        url = (m.get("image") or {}).get("url")
        if url and url not in images:
            images.append(url)
    # A price of ₹0 is almost always a typing mistake in Shopify. Never use it in a post; tell the owner instead.
    priced = [v for v in variants if v["price"] > 0]
    issues = [f"Size {v['size']} has price ₹0 in Shopify" for v in variants if v["price"] <= 0]
    if not priced:
        priced = variants or [{"price": 0.0, "compare_at": None}]
    compare = [v["compare_at"] for v in priced if v["compare_at"] and v["compare_at"] > v["price"]]
    return {
        "shopify_id": node["id"],
        "handle": node["handle"],
        "title": node["title"].strip(),
        "description": node.get("description") or "",
        "product_type": node.get("productType") or "",
        "tags": json.dumps(node.get("tags") or []),
        "collections": json.dumps([c["title"] for c in node["collections"]["nodes"]]),
        "variants": json.dumps(variants),
        "images": json.dumps(images),
        "min_price": min(v["price"] for v in priced),
        "max_price": max(v["price"] for v in priced),
        "compare_at_min": min(compare) if compare else None,
        "total_stock": node.get("totalInventory"),
        "status": node.get("status"),
        "shopify_created": node.get("createdAt"),
        "pack_size": pack_size(node["title"], node.get("tags") or []),
        "url": node.get("onlineStoreUrl"),
        "issues": json.dumps(issues),
    }


def sync_products(brand: Brand) -> SyncResult:
    """Copy every product from Shopify into the database (replacing the old copy)."""
    rows, after = [], None
    while True:
        data = graphql(brand, PRODUCTS_QUERY, {"first": PAGE_SIZE, "after": after})
        rows += [_row(n) for n in data["products"]["nodes"]]
        page = data["products"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        before = {r["shopify_id"] for r in conn.execute("SELECT shopify_id FROM products WHERE brand = ?",
                                                         (brand.slug,))}
        seen = set()
        for row in rows:
            seen.add(row["shopify_id"])
            cols = ["brand", *row.keys(), "synced_at"]
            conn.execute(
                f"INSERT OR REPLACE INTO products ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [brand.slug, *row.values(), now])
        gone = before - seen
        conn.executemany("DELETE FROM products WHERE brand = ? AND shopify_id = ?",
                         [(brand.slug, g) for g in gone])
    result = SyncResult(total=len(rows), new=len(seen - before), removed=len(gone),
                        in_stock=sum(1 for r in rows if r["status"] == "ACTIVE" and (r["total_stock"] or 0) > 0),
                        problems=sum(1 for r in rows if json.loads(r["issues"])))
    for r in rows:
        for issue in json.loads(r["issues"]):
            log.warning("Shopify data problem — %s (%s): %s", r["title"], r["handle"], issue)
    set_state(brand.slug, "last_sync", json.dumps({"at": now, **result.__dict__}))
    log.info("Shopify sync for %s: %d products (%d new, %d removed, %d active and in stock)",
             brand.slug, result.total, result.new, result.removed, result.in_stock)
    return result
