"""The keys and step-by-step help shown in the Control Center.

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
        "https://core.telegram.org/bots/features#botfather", "telegram", secret=False, optional=True,
    ),
    KeySpec(
        "GITHUB_TOKEN", "GitHub key (read-only)",
        "Lets the agent download its own updates. Starts with github_pat_",
        "https://github.com/settings/personal-access-tokens", "github",
    ),
]

KEYS_BY_NAME = {k.name: k for k in KEYS}


@dataclass(frozen=True)
class Service:
    id: str              # matches KeySpec.test and the test function name
    title: str
    purpose: str         # what it does for the agent, in one line
    cost: str
    minutes: int         # rough time to set up
    keys: list[str]      # KeySpec names shown in this card
    steps: list[str]     # click-by-click instructions (simple HTML allowed; written by us, not users)


# Order = the order the Connect page walks you through (easiest first).
SERVICES: list[Service] = [
    Service(
        "telegram", "Telegram", "Sends you each draft with Approve / Regenerate / Edit / Reject buttons.",
        "Free", 5, ["TELEGRAM_BOT_TOKEN"],
        [
            'Open Telegram and search for <b>@BotFather</b> (blue tick). Press <b>Start</b>.',
            'Send <code>/newbot</code>. For the name send <code>Skipper Packs Agent</code>.',
            'For the username send something ending in <code>bot</code>, e.g. '
            '<code>skipperpacks_agent_bot</code> (add numbers if taken).',
            'BotFather replies with a token like <code>1234567890:AAH…</code>. Copy it and paste it below.',
            'Tap the <b>t.me/…</b> link BotFather gave you and press <b>Start</b> in your new bot\'s chat.',
            'Press <b>Save &amp; test</b>. You\'ll get a "connected" message in Telegram.',
        ],
    ),
    Service(
        "anthropic", "Claude", "Plans your week and writes captions, hooks and hashtags.",
        "Pay as you go — about US$3–10 a month", 5, ["ANTHROPIC_API_KEY"],
        [
            'Open <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a> '
            'and sign up.',
            'Left menu → <b>Billing</b> → add a card and buy US$5 of credit. '
            'Tip: under <b>Limits</b> set a monthly cap, e.g. US$20.',
            'Left menu → <b>API keys</b> → <b>Create Key</b> → name it <code>skipper-agent</code>.',
            'Copy the key (starts with <code>sk-ant-</code>; it\'s shown only once) and paste it below.',
        ],
    ),
    Service(
        "gemini", "Gemini (Google)", "Makes AI images and Veo reels, and checks image quality.",
        "Pay as you go — reels are the main cost", 10, ["GEMINI_API_KEY"],
        [
            'Open <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Google AI Studio → '
            'API keys</a> and sign in with your business Google account.',
            'Click <b>Create API key</b> (choose "new project" if asked). Copy the key (starts with '
            '<code>AIza</code>) and paste it below.',
            'Turn on billing (needed for reels): click <b>Set up billing</b> next to your key and add a card.',
            'Tip: in Google Cloud → <b>Billing → Budgets &amp; alerts</b>, set a monthly budget (e.g. ₹5,000).',
        ],
    ),
    Service(
        "shopify", "Shopify", "Reads your products, prices, sizes and stock. It never changes your store.",
        "Free", 15, ["SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ADMIN_TOKEN"],
        [
            'Open your Shopify admin <b>as the store owner</b>. Copy the web address from the browser bar '
            '(looks like <code>admin.shopify.com/store/your-store</code>) into <b>Store address</b> below.',
            'Open the <a href="https://dev.shopify.com/dashboard" target="_blank" rel="noopener">Shopify Dev '
            'Dashboard</a> with the same login → <b>Create app</b> → name it <code>Skipper Social Agent</code>.',
            '<b>Versions → Create version</b> → under Access scopes tick only <code>read_products</code> and '
            '<code>read_inventory</code> → <b>Release</b>.',
            'App home → <b>Install app</b> → pick your store → <b>Install</b>.',
            'App → <b>Settings</b> → copy <b>Client ID</b> and <b>Client secret</b> and paste them below.',
            'Already have an old token starting <code>shpat_</code>? Paste only that in the last box instead.',
        ],
    ),
    Service(
        "meta", "Instagram + Facebook", "Posts approved content and reads how each post performed.",
        "Free", 25, ["META_ACCESS_TOKEN"],
        [
            'Open <a href="https://business.facebook.com/settings" target="_blank" rel="noopener">Business '
            'Settings</a>. Check your Page is under <b>Accounts → Pages</b> and your Instagram under '
            '<b>Accounts → Instagram accounts</b> (add them if not).',
            'Open <a href="https://developers.facebook.com/apps" target="_blank" rel="noopener">Meta for '
            'Developers</a> → <b>Create app</b> → name <code>Skipper Social Agent</code> → use case '
            '<b>Manage everything on your Page</b> (also add the Instagram use case if offered) → link it to '
            'your business. No App Review needed; leave it in Development mode.',
            'Back in Business Settings → <b>Users → System users</b> → <b>Add</b> → name '
            '<code>skipper-agent</code>, role <b>Admin</b>.',
            'Click it → <b>Assign assets</b>: the app, your Page and your Instagram account, each with '
            '<b>Full control</b>.',
            '<b>Generate new token</b> → pick the app → expiry <b>Never</b> → tick: '
            '<code>pages_show_list</code>, <code>pages_read_engagement</code>, <code>pages_manage_posts</code>, '
            '<code>pages_manage_engagement</code>, <code>pages_read_user_engagement</code>, '
            '<code>read_insights</code>, <code>instagram_basic</code>, <code>instagram_content_publish</code>, '
            '<code>instagram_manage_insights</code>, <code>business_management</code>, <code>ads_read</code>, '
            '<code>ads_management</code> (Meta requires the last two for posting to Instagram from a business account; '
            'the agent never runs ads).',
            'Copy the token (starts with <code>EAA</code>) and paste it below. The test finds your Page and '
            'Instagram account by itself.',
        ],
    ),
]

SERVICES.append(Service(
    "github", "Updates (GitHub)", "Lets you install new versions of the agent with one click.",
    "Free", 3, ["GITHUB_TOKEN"],
    [
        'Sign in to <a href="https://github.com" target="_blank" rel="noopener">github.com</a> with the account '
        'that owns the agent\'s code.',
        'Open <a href="https://github.com/settings/personal-access-tokens/new?name=Skipper%20Agent%20updates'
        '&description=Lets%20the%20agent%20download%20its%20own%20updates&expires_in=none&contents=read" '
        'target="_blank" rel="noopener">this ready-made key page</a> (name, "no expiry" and read-only are filled in).',
        'Under <b>Repository access</b> choose <b>Only select repositories</b> and pick <code>rdai</code>.',
        'Under <b>Permissions</b> check it says <b>Contents: Read-only</b> (nothing else). '
        'If GitHub doesn\'t allow "No expiration", pick the longest option.',
        'Click <b>Generate token</b>, copy it (starts with <code>github_pat_</code>) and paste it below.',
    ],
))

SERVICES_BY_ID = {s.id: s for s in SERVICES}
