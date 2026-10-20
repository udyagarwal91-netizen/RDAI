"""The "Save & test" checks in the Control Center.

Each test makes the smallest possible harmless request to the service
(read-only — nothing is posted or changed) and returns (ok, message).
Messages are scrubbed so a key can never leak into the page or logs.
"""

import logging

import requests

from app import brand_editor, secrets_store
from app.brands import Brand

log = logging.getLogger(__name__)
TIMEOUT = 20


def run_test(test_name: str, brand: Brand) -> tuple[bool, str]:
    func = TESTS.get(test_name)
    if func is None:
        return False, "No test available for this key."
    try:
        ok, msg = func(brand)
    except requests.RequestException as e:
        ok, msg = False, f"Could not reach the service: {type(e).__name__}. Check your internet connection."
    except Exception as e:  # any surprise still becomes a readable message
        ok, msg = False, f"Unexpected problem: {e}"
    msg = secrets_store.scrub(msg)
    log.info("Key test %s: %s — %s", test_name, "OK" if ok else "FAILED", msg)
    return ok, msg


def _missing(*names: str) -> str:
    return "Paste the " + " and ".join(names) + " first, then press Test."


def test_anthropic(brand: Brand) -> tuple[bool, str]:
    key = brand.secret("ANTHROPIC_API_KEY")
    if not key:
        return False, _missing("Claude API key")
    r = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=TIMEOUT,
    )
    if r.status_code == 200:
        return True, "Claude key works."
    if r.status_code == 401:
        return False, "Claude says this key is wrong or was deleted. Create a new one and paste it again."
    return False, f"Claude replied with error {r.status_code}."


def test_gemini(brand: Brand) -> tuple[bool, str]:
    key = brand.secret("GEMINI_API_KEY")
    if not key:
        return False, _missing("Gemini API key")
    r = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},
        params={"pageSize": 200},
        timeout=TIMEOUT,
    )
    if r.status_code == 200:
        names = [m.get("name", "") for m in r.json().get("models", [])]
        has_veo = any("veo" in n for n in names)
        extra = " Veo (reels) is available." if has_veo else " Veo (reels) not listed yet — check billing is on."
        return True, "Gemini key works." + extra
    if r.status_code in (400, 401, 403):
        return False, "Google says this key is not valid. Copy it again from AI Studio."
    return False, f"Google replied with error {r.status_code}."


def shopify_access_token(brand: Brand) -> tuple[str | None, str]:
    """Get a Shopify Admin API token.

    Old custom apps: a permanent shpat_ token. New Dev Dashboard apps: swap
    Client ID + secret for a token that lasts 24 hours (fetched fresh here;
    Phase 1 will cache it).
    """
    legacy = brand.secret("SHOPIFY_ADMIN_TOKEN")
    if legacy:
        return legacy, ""
    client_id = brand.secret("SHOPIFY_CLIENT_ID")
    client_secret = brand.secret("SHOPIFY_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None, _missing("Shopify Client ID", "Client secret")
    domain = brand.config["shopify"]["store_domain"]
    r = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=TIMEOUT,
    )
    if r.status_code == 200 and r.json().get("access_token"):
        return r.json()["access_token"], ""
    body = r.text[:300]
    if "shop_not_permitted" in body:
        return None, ("Shopify refused: the app and the store are not in the same Shopify organization. "
                      "See docs/SETUP_GUIDE.md, section 4 (end).")
    if r.status_code in (400, 401):
        return None, "Shopify rejected the Client ID / secret. Check both, and that the app is installed on the store."
    return None, f"Shopify replied with error {r.status_code}. Is the app installed on your store?"


def test_shopify(brand: Brand) -> tuple[bool, str]:
    domain = brand.config["shopify"]["store_domain"]
    if not domain or "your-store" in domain:
        return False, "Fill in the Store address box first (copy it from your Shopify admin's browser bar)."
    token, problem = shopify_access_token(brand)
    if not token:
        return False, problem
    version = brand.config["shopify"].get("api_version", "2026-10")
    r = requests.post(
        f"https://{domain}/admin/api/{version}/graphql.json",
        json={"query": "{ shop { name } productsCount { count } }"},
        headers={"X-Shopify-Access-Token": token},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return False, f"Shopify replied with error {r.status_code}."
    payload = r.json()
    if payload.get("errors"):
        return False, ("Connected, but the app is missing permissions. In the Dev Dashboard, add the read_products "
                       "and read_inventory scopes, release a new version, and reinstall.")
    shop = payload["data"]["shop"]["name"]
    count = payload["data"].get("productsCount", {}).get("count", "?")
    return True, f"Connected to '{shop}' — {count} products found."


def test_meta(brand: Brand) -> tuple[bool, str]:
    token = brand.secret("META_ACCESS_TOKEN")
    if not token:
        return False, _missing("Meta token")
    ver = brand.config["meta"].get("graph_api_version", "v26.0")
    base = f"https://graph.facebook.com/{ver}"
    r = requests.get(f"{base}/me", params={"fields": "id,name", "access_token": token}, timeout=TIMEOUT)
    if r.status_code != 200:
        err = r.json().get("error", {}).get("message", f"error {r.status_code}")
        return False, f"Meta rejected the token: {err}"
    who = r.json().get("name", "unknown")

    page_id = str(brand.config["meta"].get("facebook_page_id") or "")
    if not page_id:
        # Find the Page ourselves from the Pages this token can manage.
        r = requests.get(f"{base}/me/accounts", params={
            "fields": "id,name,instagram_business_account", "limit": 100, "access_token": token}, timeout=TIMEOUT)
        pages = r.json().get("data", []) if r.status_code == 200 else []
        if not pages:
            return False, (f"Token works (belongs to '{who}') but it can't see any Facebook Page. In Business "
                           "Settings → System users → Assign assets, give it your Page with Full control, then "
                           "generate a new token.")
        matches = [p for p in pages if brand.name.lower().replace(" ", "") in p["name"].lower().replace(" ", "")]
        if len(pages) == 1 or len(matches) == 1:
            page_id = (pages[0] if len(pages) == 1 else matches[0])["id"]
            brand_editor.update_config(brand.slug, {"meta.facebook_page_id": page_id})
        else:
            names = ", ".join(f"{p['name']} ({p['id']})" for p in pages)
            return False, (f"Token works and can see several Pages: {names}. Go to Brand → Accounts and type "
                           "the number of the right Page, then test again.")

    r = requests.get(f"{base}/{page_id}", params={"fields": "name,instagram_business_account",
                                                   "access_token": token}, timeout=TIMEOUT)
    if r.status_code != 200:
        return False, (f"Token works, but can't open Facebook Page {page_id}. Was the Page assigned to the "
                       "system user (Business Settings → System users → Assign assets)?")
    page = r.json()
    ig = (page.get("instagram_business_account") or {}).get("id")
    if not ig:
        return False, (f"Page '{page.get('name')}' has no Instagram Business account linked. In the Instagram "
                       "app: Settings → Account type and tools → make sure it's a Professional account and "
                       "connected to this Page.")
    if str(brand.config["meta"].get("instagram_account_id") or "") != ig:
        brand_editor.update_config(brand.slug, {"meta.instagram_account_id": ig})
    return True, f"Connected to Facebook Page '{page.get('name')}' and its Instagram account. Saved both for you."


def test_telegram(brand: Brand) -> tuple[bool, str]:
    token = brand.secret("TELEGRAM_BOT_TOKEN")
    if not token:
        return False, _missing("Telegram bot token")
    api = f"https://api.telegram.org/bot{token}"
    r = requests.get(f"{api}/getMe", timeout=TIMEOUT)
    if r.status_code != 200:
        return False, "Telegram says this bot token is wrong. Copy it again from @BotFather."
    bot = r.json()["result"]["username"]

    chat_id = brand.secret("TELEGRAM_CHAT_ID")
    if not chat_id:
        # Find the chat ID from the last message someone sent the bot.
        r = requests.get(f"{api}/getUpdates", timeout=TIMEOUT)
        updates = r.json().get("result", []) if r.status_code == 200 else []
        chats = [u["message"]["chat"] for u in updates
                 if "message" in u and u["message"]["chat"].get("type") == "private"]
        if not chats:
            return False, (f"Bot @{bot} works. Now open Telegram, find @{bot}, press Start (or send 'hi'), "
                           "then press Test again so I can find your chat ID.")
        chat_id = str(chats[-1]["id"])
        prefix = brand.config.get("env_prefix", "") or ""
        secrets_store.save(prefix + "TELEGRAM_CHAT_ID", chat_id)

    r = requests.post(f"{api}/sendMessage", json={
        "chat_id": chat_id,
        "text": f"✅ {brand.name} agent is connected. Drafts for approval will arrive here.",
    }, timeout=TIMEOUT)
    if r.status_code != 200:
        return False, f"Bot @{bot} works, but couldn't message chat {chat_id}. Did you press Start in the bot chat?"
    return True, f"Bot @{bot} works. A test message was sent to your Telegram."


TESTS = {
    "anthropic": test_anthropic,
    "gemini": test_gemini,
    "shopify": test_shopify,
    "meta": test_meta,
    "telegram": test_telegram,
}
