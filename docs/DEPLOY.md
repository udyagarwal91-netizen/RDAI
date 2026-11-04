# Moving the agent online (Railway) — use it from your phone, anywhere

Cost: about **US$5/month** (Railway Hobby plan) plus your usual Claude/Gemini usage.
Time: about 30–45 minutes, once. Your laptop is no longer needed afterwards.

> ⚠️ **Only one copy may run.** When the online agent is working, close the agent on your laptop for good
> (close the black window). Two copies would post everything twice. If it ever happens, Telegram warns you.

## 1. Create the service
1. Go to https://railway.com → **Login with GitHub** (the same GitHub account that has the agent).
2. **New Project → Deploy from GitHub repo** → pick **rdai**.
3. Open the new service → **Settings → Source → Branch** → choose `claude/new-session-dm5omt`.
   Railway sees the `Dockerfile` and builds it (the first build takes ~5 minutes).
   From now on, every new version I publish installs itself — no Update button needed online.

## 2. Add the permanent disk (keeps your data, settings and pictures)
1. In the service: **Settings → Volumes → Add volume** (or right-click the service → Attach volume).
2. Mount path: `/data` → **Add**.

## 3. Paste your keys
Service → **Variables** → **Raw Editor** → paste the contents of your laptop's `.env` file
(open `C:\SkipperAgent\.env` in Notepad → Ctrl+A → Ctrl+C), then **Update variables**.
Then add these lines too (Raw Editor again):

```
SETTINGS_PASSWORD=choose-a-strong-password-for-the-online-app
LAN_ACCESS=0
```

(`DATA_DIR`, `LOGS_DIR`, `ENV_FILE`, `BRANDS_DIR` and the time zone are already set by the Dockerfile.)

## 4. Get your web address
**Settings → Networking → Generate Domain**. You get an address like
`https://skipper-agent-production.up.railway.app`. Open it, log in with the password from step 3.

## 5. Put it on your phone
- iPhone (Safari): open the address → **Share → Add to Home Screen**.
- Android (Chrome): open the address → **⋮ → Add to Home screen / Install app**.
It opens like an app, with voice notes in **Chat** (tap 🎙 Speak).

## 6. First run online
1. **Connect accounts → Check all connections** (everything should turn green — same keys as the laptop).
2. **Brand** page: check your settings (they start from the brand files; logo too).
3. **Products → Get products from Shopify**, then **Team → Full team meeting** to make this week's plan.
4. Close the laptop agent for good.

## What's different online
- Plans, drafts and history start fresh (the laptop's database stays on the laptop).
- Settings you change in the app are saved on the `/data` disk and survive new versions.
- The page is only reachable over https, with your password, login lockout and https-only cookies.

## Render (alternative)
Same idea: **New → Web Service** → connect the repo (it uses the Dockerfile) → **Environment** for the same
variables → add a **Disk** mounted at `/data` (paid instance needed; the free tier sleeps and would miss posts).
