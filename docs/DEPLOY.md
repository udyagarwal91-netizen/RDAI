# Moving to Railway (when you want it running 24/7)

You chose laptop-only for now. This page is ready for when you switch.
Cost: about US$5/month on Railway's Hobby plan.

## 1. Create the service
1. Sign up at https://railway.com with your GitHub account.
2. **New Project → Deploy from GitHub repo** → choose this repository.
3. Railway detects Python and runs `python run.py` (we'll add the start
   command file during deployment if needed).

## 2. Add a disk so the database survives restarts
1. In the service, open **Settings → Volumes → Add volume**.
2. Mount path: `/data`.

## 3. Paste your keys (Railway's "Variables" screen)
Service → **Variables** tab → **New Variable**, one per line. Same names as
your `.env` file:

| Name | Value |
|------|-------|
| `ANTHROPIC_API_KEY` | your Claude key |
| `GEMINI_API_KEY` | your Gemini key |
| `SHOPIFY_CLIENT_ID` | from Shopify Dev Dashboard |
| `SHOPIFY_CLIENT_SECRET` | from Shopify Dev Dashboard |
| `META_ACCESS_TOKEN` | your Meta system user token |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your chat ID (shown on your laptop's Settings page) |
| `SETTINGS_PASSWORD` | a password for the online Settings page |
| `FLASK_SECRET_KEY` | any long random text (e.g. mash the keyboard for 40 characters) |
| `DATA_DIR` | `/data` |
| `LOGS_DIR` | `/data/logs` |
| `ENV_FILE` | `/data/.env` |
| `COOKIE_SECURE` | `1` |

Tip: Railway's **Raw Editor** button lets you paste your whole local `.env`
in one go — then add the last five rows above.

Click **Deploy**. Under **Settings → Networking → Generate Domain** you get
a web address for the Settings page.

## Render (alternative)
Same idea: **New → Web Service** → connect the repo → Start command
`python run.py` → **Environment** tab for the same variables → add a **Disk**
mounted at `/data` (needs a paid instance; the free tier sleeps and would
miss scheduled posts).
