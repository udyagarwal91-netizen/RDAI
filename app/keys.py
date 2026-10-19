"""The list of keys shown on the Settings page.

To add a new key later, add one KeySpec here and, if it can be tested,
one test function in app/key_tests.py with the same `test` name.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class KeySpec:
    name: str          # the name in .env, e.g. GEMINI_API_KEY
    label: str         # what the Settings page calls it
    where: str         # one-line "where do I get this"
    link: str          # the exact page to get it from
    test: str | None   # which test button checks it (see key_tests.py)
    secret: bool = True  # False = safe to show in full (e.g. a chat ID)
    optional: bool = False


KEYS: list[KeySpec] = [
    KeySpec(
        "ANTHROPIC_API_KEY", "Claude API key",
        "Anthropic Console → API Keys → Create Key. Starts with sk-ant-",
        "https://console.anthropic.com/settings/keys", "anthropic",
    ),
    KeySpec(
        "GEMINI_API_KEY", "Gemini API key",
        "Google AI Studio → Get API key → Create API key. Turn on billing for reels.",
        "https://aistudio.google.com/apikey", "gemini",
    ),
    KeySpec(
        "SHOPIFY_CLIENT_ID", "Shopify Client ID",
        "Shopify Dev Dashboard → your app → Settings → Client ID",
        "https://dev.shopify.com/dashboard", "shopify", secret=False,
    ),
    KeySpec(
        "SHOPIFY_CLIENT_SECRET", "Shopify Client secret",
        "Shopify Dev Dashboard → your app → Settings → Client secret",
        "https://dev.shopify.com/dashboard", "shopify",
    ),
    KeySpec(
        "SHOPIFY_ADMIN_TOKEN", "Shopify Admin token (old apps only)",
        "Only if you already have an old custom-app token starting shpat_. Otherwise leave empty.",
        "https://help.shopify.com/en/manual/apps/app-types/custom-apps", "shopify", optional=True,
    ),
    KeySpec(
        "META_ACCESS_TOKEN", "Meta (Instagram + Facebook) token",
        "Business Settings → System users → Generate new token (never expire)",
        "https://business.facebook.com/settings/system-users", "meta",
    ),
    KeySpec(
        "TELEGRAM_BOT_TOKEN", "Telegram bot token",
        "In Telegram, message @BotFather and send /newbot",
        "https://t.me/BotFather", "telegram",
    ),
    KeySpec(
        "TELEGRAM_CHAT_ID", "Your Telegram chat ID",
        "Message your bot once, then press Test — the agent finds it for you",
        "https://core.telegram.org/bots/features#botfather", "telegram", secret=False,
    ),
]

KEYS_BY_NAME = {k.name: k for k in KEYS}
