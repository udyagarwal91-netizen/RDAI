# Skipper Packs — Social Media Content Agent

An always-on helper that reads your Shopify products, plans a week of
Instagram + Facebook posts, makes the images/reels, sends each draft to you on
Telegram for ✅ approval, posts the approved ones on time, and learns from
what performed well.

**Status:** Phases 0–4 are built: setup, products + weekly plan, drafts (captions, brand images, carousels,
AI images, AI reels) with quality checks and Telegram approval, automatic posting to Instagram and Facebook,
and weekly performance reports whose lessons feed the next plan. Next: Phase 5 extras. Run it, connect your
accounts, then press **Get products from Shopify** and **Make this week's plan** — or let the timer
do it every week.

## The Control Center

Double-click the start file and it opens in your browser:

| Page | What you do there |
|------|-------------------|
| **Home** | See what's done, the one next step, and the action buttons (they switch on as each phase is built) |
| **Plan** | This week's planned posts with product photo, Shopify price, idea and hook; ⚠️ marks anything to check |
| **Drafts** | Finished posts waiting for you: pictures, caption, ⚠️ checks, Approve / Regenerate / Reject |
| **Queue** | Approved posts and when they go out; Post now / cancel; links to what's been posted |
| **Performance** | Reach, saves, shares per post; what works best by format, topic, style and time; the agent's lessons |
| **Products** | Everything the agent knows from Shopify, grouped by style, plus any price mistakes found in your store |
| **Connect accounts** | Step-by-step help for Telegram, Claude, Gemini, Shopify, Instagram/Facebook — paste, press **Save & test** |
| **Brand** | Store address, posting days/times, content mix, colours, logo upload, voice, hashtags, banned words, approval mode, brand rules |
| **Activity** | What the agent did each day, newest first |

---

## How the pieces connect (plain English)

Everything runs as **one program** (`run.py`). Inside it, a built-in
**scheduler** wakes up at set times. On Sunday it pulls your products from
**Shopify** into a small **database** file, asks **Claude** to plan the week
(using your brand folder's rules and last week's results), and for each
planned post makes the visual — either a **brand template** turned into an
image, or an **AI image/reel from Gemini** using your real product photo as the
reference. A **checker** compares the result against the real product and
checks prices against Shopify, then the **Telegram bot** sends you the draft
with buttons. Approved posts go into a **queue**; at the scheduled time the
**publisher** posts them to Instagram and Facebook through Meta. Every week the
**learner** collects reach/saves/shares from Meta and feeds a summary into the
next week's plan. All brand-specific settings live in `brands/<brand>/`, all
keys in `.env` (edited in the **Control Center**), and every action is
written to a daily log file in `logs/`.

```
Shopify ──► products ──► Planner (Claude) ──► Producer (templates / Gemini) ──► Checker
                                ▲                                                  │
                                │                                                  ▼
                     Learner (Meta insights)                     Telegram: ✅ 🔁 ✏️ ❌
                                ▲                                                  │
                                └──────── Publisher (Instagram + Facebook) ◄───────┘
```

## Folder map

```
RDAI/
├── run.py                  ← starts everything
├── start-windows.bat       ← double-click to start (Windows)
├── start-mac.command       ← double-click to start (Mac)
├── .env.example            ← key slots template (your real keys go in .env, never in Git)
├── requirements.txt        ← the few libraries the agent uses
├── brands/
│   └── skipperpacks/
│       ├── config.yaml     ← store, accounts, posting times, colours, hashtags, auto_publish
│       ├── brand-rules.md  ← your brand voice and hard rules, in plain English
│       ├── templates/      ← post designs (Phase 2)
│       ├── examples/       ← posts you like, for style hints
│       └── assets/         ← logo.png, fonts
├── app/
│   ├── secrets_store.py    ← reads/saves keys; masks them
│   ├── keys.py             ← the keys + step-by-step help shown in the app
│   ├── key_tests.py        ← the "Save & test" checks
│   ├── brands.py           ← loads brand folders
│   ├── db.py               ← database tables
│   ├── logging_setup.py    ← daily log files, keys scrubbed
│   ├── shopify.py          ← reads products, prices, sizes, stock (read-only)
│   ├── planner.py          ← builds the weekly plan with Claude and checks it against Shopify
│   ├── calendar_india.py   ← festivals and sale days the planner uses
│   ├── writer.py           ← writes captions, hashtags and the picture brief (Claude)
│   ├── renderer.py         ← turns design templates into post images
│   ├── gemini.py           ← AI images from the real product photo
│   ├── checker.py          ← final quality check of pictures and caption
│   ├── video.py            ← AI reels: Gemini clip + the agent's own text, price and logo layers
│   ├── insights.py         ← post results from Meta, weekly report, lessons for the planner
│   ├── producer.py         ← makes a finished draft: words → pictures → check → save
│   ├── factcheck.py        ← checks prices and pack sizes in any text against Shopify
│   ├── default_templates/  ← the post and carousel designs
│   ├── publisher.py        ← posts approved drafts to Instagram + Facebook on time; retries; alerts
│   ├── telegram.py         ← sends you plans and drafts; handles buttons and commands
│   ├── jobs.py             ← runs actions in the background + the weekly timer
│   ├── brand_editor.py     ← saves Brand-page changes into config.yaml safely
│   ├── status.py           ← remembers which connections passed
│   └── web/                ← the Control Center (pages + forms)
│   (coming: producer, checker, publisher, learner)
├── docs/
│   ├── SETUP_GUIDE.md      ← how to get each key, click by click
│   └── DEPLOY.md           ← moving to Railway later
├── data/                   ← database + generated images (created automatically, not in Git)
├── logs/                   ← one log file per day (not in Git)
└── tests/                  ← automatic checks
```

**Why Python:** it's the easiest language to read, and every service we use
(Shopify, Meta, Telegram, Claude, Gemini, Playwright, FFmpeg) works well with it.

---

## Run it on your laptop

### Step 1 — install Python (one time)
- **Windows:** go to https://www.python.org/downloads/ → download Python 3.12
  or newer → run it → **tick "Add python.exe to PATH"** on the first screen →
  Install Now.
- **Mac:** go to https://www.python.org/downloads/ → download → open the
  `.pkg` → click through.

### Step 2 — get the project
On GitHub, open this repository → green **Code** button → **Download ZIP**.
Unzip it, and put the contents in a short folder: on Windows **`C:\SkipperAgent`** (deep folders inside
Downloads can hit Windows' path-length limit), on a Mac your home folder is fine.

### Step 3 — start it
- **Windows:** double-click `start-windows.bat`.
- **Mac:** right-click `start-mac.command` → **Open** → **Open** (Mac asks the
  first time). If it says "not permitted", open the Terminal app, type `bash `
  (with a space), drag the file into the window, press Enter.

The first start takes 2–3 minutes. Your browser then opens
**http://localhost:8000**. Create a password, then follow
the **Next step** button on the Home page — it walks you through everything.

**Updates:** once you've added the GitHub key (Connect accounts → Updates), press **Update agent** on the
Home page. It downloads the newest version, keeps your keys, data and brand settings, and restarts itself.

**To stop:** close the black window (or press Ctrl+C in it).
**To see what happened on a day:** open `logs/2026-10-20.log` (any date) in
Notepad/TextEdit.

> While running on your laptop, posts only go out when the laptop is on,
> awake and connected. Move to Railway (docs/DEPLOY.md) when you want it 24/7.

---

## For developers

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py          # Control Center on http://localhost:8000
.venv/bin/python -m pytest -q    # all tests
.venv/bin/python -m pytest -q tests/test_phase0.py::test_mask   # one test
```
