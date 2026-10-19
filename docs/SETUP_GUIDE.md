# Setup Guide — getting your 5 keys

You need five accounts connected. Do them in this order; each takes 5–15
minutes. After each one, paste the key on the **Settings page**
(http://localhost:8000) and press **Test**. Green ✅ = done.

| # | Service | What it does for the agent | Cost |
|---|---------|----------------------------|------|
| 1 | Telegram | Sends you drafts to approve | Free |
| 2 | Claude (Anthropic) | Plans the week, writes captions | Pay as you go (US$5 minimum top-up) |
| 3 | Gemini (Google) | AI images and Veo reels | Pay as you go (billing needed for reels) |
| 4 | Shopify | Reads products, prices, stock | Free (your existing store) |
| 5 | Meta | Posts to Instagram + Facebook | Free |

> **Golden rule:** a key is like a password to your account. Paste it only
> into the Settings page. Never send it on WhatsApp, email, or Telegram,
> and never paste it into ChatGPT/Claude chats.

---

## 1. Telegram bot (5 minutes)

1. Open Telegram on your phone or computer.
2. Search for **@BotFather** (blue tick) and open the chat. Press **Start**.
3. Send: `/newbot`
4. It asks for a name. Send: `Skipper Packs Agent`
5. It asks for a username ending in `bot`. Send something like:
   `skipperpacks_agent_bot` (if taken, add numbers).
6. BotFather replies with a token that looks like
   `1234567890:AAH...`. Copy it.
7. Settings page → **Telegram bot token** → paste → **Save**.
8. In Telegram, tap the link BotFather gave you (t.me/your_bot) and press
   **Start**. This lets the bot message you.
9. Settings page → Telegram → **Test**. The agent finds your chat ID by
   itself and sends you "✅ Skipper Packs agent is connected".

---

## 2. Claude API key (5 minutes)

1. Go to https://console.anthropic.com and sign up (use your work email).
2. Left menu → **Billing** → add a card and buy credits (US$5 is enough
   to start; the agent will use roughly US$3–10 a month).
3. Left menu → **API keys** → **Create Key** → name it `skipper-agent`.
4. Copy the key (starts with `sk-ant-`). You can only see it once.
5. Settings page → **Claude API key** → paste → **Save** → **Test**.

Tip: in **Billing → Limits**, set a monthly spend limit (e.g. US$20) so
there are no surprises.

---

## 3. Gemini API key (10 minutes)

1. Go to https://aistudio.google.com/apikey and sign in with a Google account
   (use a business account you'll keep long term).
2. Click **Create API key**. If asked, choose **Create API key in new
   project**.
3. Copy the key (starts with `AIza`).
4. **Turn on billing** (needed for Veo reels and higher image limits): in AI
   Studio, open **Billing** (or the "Set up billing" link next to your key)
   and add a card. Google may give free credits for new accounts.
5. Settings page → **Gemini API key** → paste → **Save** → **Test**. The
   test also tells you whether Veo (reels) is available on your key.

Tip: in Google Cloud Billing → **Budgets & alerts**, set a monthly budget
(e.g. ₹5,000) so you get an email before spending more. With 4 reels a week,
expect roughly ₹500–₹5,500 a month for reels alone depending on the Veo tier
we pick (exact prices confirmed before Phase 2b).

---

## 4. Shopify (15 minutes)

Shopify changed this in January 2026: apps are now made in the **Dev
Dashboard**, not inside the store admin. You get a **Client ID** and **Client
secret**; the agent swaps them for a fresh access key every day by itself.

> Already have an old custom app with a token starting `shpat_`? Paste that
> into **Shopify Admin token (old apps only)** and skip to step 7.

1. Log in to your Shopify admin **as the store owner**.
2. Go to **Settings → Apps → Develop apps** and click the button to build
   apps in the **Dev Dashboard** (or go straight to https://dev.shopify.com/dashboard
   and log in with the same owner account).
3. Click **Create app** → name it `Skipper Social Agent`.
4. Open **Versions → Create version**. Under **Access scopes**, tick:
   - `read_products`
   - `read_inventory`

   Nothing else — the agent never changes your store. Click **Release**.
5. Go back to the app's home page and click **Install app** → choose your
   Skipper Packs store → **Install**.
6. Open the app's **Settings**. Copy the **Client ID** and **Client secret**.
7. Find your store's `.myshopify.com` address: Shopify admin →
   **Settings → Domains** (looks like `skipperpacks-xyz.myshopify.com`).
   Open `brands/skipperpacks/config.yaml` in Notepad/TextEdit and replace
   `your-store.myshopify.com` with it. Save.
8. Settings page → paste Client ID and Client secret → **Save** → **Test**.
   You should see "Connected to 'Skipper Packs' — N products found".

**If Test says "not in the same Shopify organization":** the Dev Dashboard
account and the store belong to different Shopify organizations. Log in to the
Dev Dashboard with the store owner's login and check the store appears under
**Stores**. If you still can't, tell me — we'll use the other method.

---

## 5. Meta — Instagram + Facebook (20–30 minutes)

You told me Instagram is already a Business account linked to the Facebook
Page. 

We'll use a **System User token**, which never expires (normal tokens die
every 60 days and posting would silently stop).

### 5a. Make sure you have a Business Portfolio
1. Go to https://business.facebook.com/settings
2. If asked, create a business portfolio called "Skipper Packs".
3. Under **Accounts → Pages**, check your Skipper Packs Page is listed.
   If not: **Add → Add a Page** → pick it.
4. Under **Accounts → Instagram accounts**, check your Instagram is listed.
   If not: **Add** → log in to Instagram.

### 5b. Create a Meta app
1. Go to https://developers.facebook.com/apps → **Create app**.
2. Name: `Skipper Social Agent`. Contact email: yours.
3. Use case: choose **Manage everything on your Page** and also add the
   Instagram use case (**Manage messaging & content on Instagram**) when offered.
4. When asked for a business portfolio, choose **Skipper Packs**.
5. Finish. You do **not** need to submit for App Review — you're only
   posting to your own accounts. Leave the app in **Development** mode.

### 5c. Create the System User and token
1. Go to https://business.facebook.com/settings/system-users
2. **Add** → name `skipper-agent` → role **Admin** → Create.
3. Click the new system user → **Assign assets**:
   - **Apps** → `Skipper Social Agent` → **Full control**
   - **Pages** → Skipper Packs → **Full control**
   - **Instagram accounts** → your account → **Full control**
4. Click **Generate new token** → choose app `Skipper Social Agent` →
   expiry **Never** → tick these permissions:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `read_insights`
   - `instagram_basic`
   - `instagram_content_publish`
   - `instagram_manage_insights`
   - `business_management`
5. **Generate** → copy the token (long text starting `EAA`). Shown once.
6. Settings page → **Meta token** → paste → **Save** → **Test**.

### 5d. Page ID and Instagram ID
1. Facebook Page ID: open your Page on Facebook → **About** → **Page
   transparency** → copy **Page ID**. Or ask me and I'll show another way.
2. Open `brands/skipperpacks/config.yaml` → under `meta:` paste it into
   `facebook_page_id: "..."` (keep the quotes). Save.
3. Settings page → Meta → **Test** again. It will tell you your Instagram
   account ID; paste it into `instagram_account_id: "..."` and Test once
   more until it's green.

---

## When all five are green ✅

Tell me "Phase 0 done". I'll then build Phase 1: pulling your products from
Shopify and sending you this week's content plan on Telegram.
