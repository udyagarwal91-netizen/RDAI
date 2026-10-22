# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A social media content agent for the innerwear brand Skipper Packs (and, later, client garment brands):
Shopify products → weekly plan (Claude) → images/reels (HTML templates via Playwright, Gemini images, Veo reels)
→ quality/price/safety checks → Telegram approval → Meta Graph API publishing → weekly insights fed back into planning.
The owner is not a coder: user-facing text, docs and error messages must be plain English with exact click paths.

Build roadmap (one phase at a time, confirm with the owner after each): 0 setup ✅ → 1 Shopify sync + weekly plan
on Telegram ✅ → 2 captions/images/carousels/Gemini images + QC + approval ✅ → 2b reels ✅ → 3 publishing ✅ → 4 insights ✅ (2b/3/4 awaiting owner's live test)
→ 5 extras. The full brief is the owner's original prompt; key decisions so far: laptop-only hosting for now
(deploy-ready for Railway), captions in English with light Hinglish, max 4 reels/week, AI images may mix
product-only / dressed-model / website-style model shots (owner chose a mix; brand form can restrict).
Veo 3.1 preview models shut down 2026-10-22; reels use `gemini-omni-1.1-flash` (~US$0.10/s at 720p).

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py                     # Control Center at http://localhost:8000 (NO_BROWSER=1 to skip opening a browser)
.venv/bin/python -m pytest -q               # all tests (CHROMIUM_PATH=/path/to/chrome also runs the real render test)
.venv/bin/python -m pytest -q tests/test_phase0.py::test_mask   # single test
```

To run without touching the real `.env`/data, set `ENV_FILE`, `DATA_DIR`, `LOGS_DIR` to a scratch location.

## Architecture

- `run.py` — single entry point; loads `.env`, sets up logging + DB, validates brands, starts the APScheduler timer
  (`jobs.start_scheduler`) and one Telegram `Bot` poller per brand, then serves the Flask Control Center.
- `app/shopify.py` — Admin GraphQL (paged 10/request for query cost), token caching for 24h client-credentials
  tokens, `sync_products` replaces the brand's `products` rows. Variants priced ≤ 0 are excluded from prices and
  recorded in `products.issues` (the real store has one). Pack size parsed from title/tags ("3 Pcs Pack").
- `app/planner.py` — groups products by title into "styles" (the store has one product per colour with identical
  titles), computes slots from `schedule.slots`, target counts from `content_mix` (reel cap enforced in code),
  calls `claude-opus-5-5` via `client.beta.messages.create` with `fallbacks="default"`, a JSON-schema
  `output_config.format` (enums constrain slot/style ids) and a cached system prompt; retries once if slots are
  missing; `check_post` flags ₹ amounts not in Shopify, % discounts, banned and needs-proof words. A new plan
  replaces only `status='planned'` rows from its first slot on.
- `app/jobs.py` — the single place actions live (`sync`, `plan`, `send_plan`, `update`, `drafts`, `image`, `reel`, `regen`,
  `pause`, `resume`, `stats`); used by Control Center buttons
  (background threads, status in `app_state` `job:<action>`) and the timer (weekly plan, daily 06:15 sync).
- `app/telegram.py` — HTML messages split under 4096 chars; `Bot` long-polls `getUpdates` and ignores every chat
  except `TELEGRAM_CHAT_ID` (it idles until that is set, so the Settings test can still discover the chat ID).
- `app/updater.py` — one-click updates: GitHub API (fine-grained `GITHUB_TOKEN`, contents:read) → zipball of
  `UPDATE_BRANCH` → copied over the install, skipping `.env`, `.venv`, `data/`, `logs/`, `media/`; under `brands/`
  only NEW files are added. Then `os._exit(3)`; the start scripts loop on exit code 3 to restart.
  **Consequence: existing brand `config.yaml` files are never upgraded — any new config key must be read with a
  default (`.get(..., default)`) and should get a Control Center form.**
- Phase 2 pipeline (`producer.make_draft`): `writer.write` (Claude, sees up to 4 product photos by URL, returns
  caption/hashtags/headline/subline/photo_index/image_style/scene/slides; `factcheck.check_text` → one rewrite with
  feedback) → `make_visuals` (`renderer.render` HTML templates via Playwright for template_image/carousel —
  prices on images come from Shopify via `planner.price_text`, never from the AI; or `gemini.generate_image` with the
  real product photo as reference, then `media.to_jpeg` 1080×1350 + logo stamp) → `checker.check` (Claude vision;
  on failure one automatic fix: remake AI image with the problems, or rewrite caption + re-render) → saved under
  `DATA_DIR/media/<brand>/drafts/<id>/`. Reel slots become AI images until Phase 2b.
- `app/gemini.py` — Interactions API (`POST v1beta/interactions`, `response_format` with aspect_ratio/image_size),
  default `gemini-nano-banana-2.1`; image is read only from `model_output` steps (the answer can echo our input).
- `app/claude.py` — shared `ask()` (structured JSON output, `fallbacks="default"`, cached system prompt, plain-English
  `ClaudeError`, cost). Planner, writer and checker all use it; tests swap `claude.client_for`.
- Approval: Telegram `send_draft` (photo or album, then caption message with ✅🔁✏️❌ `callback_data` `a|r|e|x:<id>`);
  `Bot.handle_button` only accepts the owner's chat; ✏️ sets `app_state awaiting_caption`; decisions go through
  `producer.decide` (→ `approvals` table). Same actions on the Control Center Drafts page.
- `app/publisher.py` — approving (`producer.decide` approve/auto) calls `queue_draft`: one `posts` row per platform
  at the draft's time (unscheduled → `next_free_slot`; past → now). `jobs.publish_tick` runs every minute (APScheduler
  interval) → `publish_due`: skips when paused (`app_state paused`), marks posts >6h late as `missed` and asks on
  Telegram (📤 Post now `p:<post_id>` / ✖️ Skip `s:<post_id>`), retries failures at +5/+10 min then alerts with
  🔁 Try again. Instagram needs a public image URL, so pictures are first uploaded to the Page as unpublished photos
  (`published=false`, URL from the photo's `images` field; cached in `app_state fbphotos:<draft>:<attempt>`) — no
  extra hosting. Facebook: single photo via `/{page}/photos`, multi via `/{page}/feed` + `attached_media`. Uses a Page
  token fetched with the System User token. Meta permissions needed are listed in `keys.SERVICES` (meta) and checked by
  `key_tests._missing_permissions`.
- Regenerate takes an optional owner note (Telegram 🔁 → `app_state awaiting_regen` → next message or /skip; Drafts
  page note box) → `producer.remake(note=)` → writer prompt + Gemini `extra_instructions`. Remake keeps the draft's
  own format.
- Home tiles go live only when `phase <= CURRENT_PHASE` AND the key has an action (`ready` set).
- Timer: drafts daily 07:30 for posts due within 2 days (`jobs.drafts(days=2)`).
- Updater never replaces `start-windows.bat` / `start-mac.command` (they are running during the update). Anything new
  needed at start-up must be done from Python (e.g. `renderer.start_browser_install`).
- Reels (`app/video.py`): `generate_clip` → Interactions API, `response_format {type: video, aspect_ratio 9:16,
  resolution 720p}`, `generation_config.video_config.task = reference_to_video` (product photo as subject reference);
  video read from `model_output` (inline `data` or `uri` → Files API download). `compose` lays transparent PNG layers
  rendered from `default_templates/reel_overlay.html` (hook / price / end card / logo, `renderer.render(transparent=True)`)
  over the clip with bundled FFmpeg (`imageio-ffmpeg`) → 1080×1920 H.264/AAC (silent track added if none). `frames()`
  feeds the checker. Drafts store `reel.mp4` + `cover.jpg` (`d["is_reel"]`, `d["thumb_n"]`). Weekly cap =
  `content_mix.max_reels_per_week` counted over drafts from the last 7 days; over the cap or on Gemini failure the
  draft falls back to an AI image with a note. Publishing: Instagram `media_type=REELS, upload_type=resumable` +
  POST the file to `rupload.facebook.com/ig-api-upload/<ver>/<container>` (headers Authorization: OAuth, offset,
  file_size); Facebook `graph-video.facebook.com/<ver>/<page>/videos` (post id from `fields=post_id`).
- `app/insights.py` — `collect` (nightly 23:10 + before each plan + "Refresh" button): IG media insights
  (reach, likes, comments, saved, shares, views, total_interactions, profile_visits, follows; reels add
  ig_reels_avg_watch_time) and FB post insights (post_total_media_view_unique, post_media_view, post_clicks) + post
  fields for reactions/comments/shares. Metrics Meta rejects are skipped (`_rejected`). `report` groups by
  format/topic/AI style/time; `make_lessons` (Claude, needs ≥3 posts with reach) stores `app_state lessons`;
  `for_planner` is what `planner.performance_summary` returns. Monday 09:05 `jobs.stats` sends the report.
- Chat (`app/assistant.py`): owner's own words (Telegram text, voice, Control Center Chat page) → manual Claude
  tool loop (`claude.chat`, effort medium, ≤8 steps, response.content appended as-is) over `TOOLS` that wrap the same
  actions (`jobs.start`, producer, publisher, insights, brand_editor). Tools in `NEEDS_CONFIRMATION` (approve, reject,
  post now, cancel, resume, remember_instruction → "Owner's notes" in brand-rules.md) are never run by Claude: they
  become `app_state pending:<token>` (2h expiry, deleted when answered) → Telegram buttons `k|n:<token>` / web
  `/chat/confirm/<token>/<yes|no>` → `assistant.confirm`. History: `app_state chat_history` (plain text, last 16 turns).
  Telegram: unknown non-command text → `Bot.chat`; voice/audio → `download_file` (getFile; URL holds the token, never
  logged) → `voice.transcribe` (Gemini Interactions `gemini-3.8-flash`, audio input, ≤3 min; config `ai_voice.model`)
  → echoed "🎙 I heard" → same path (also feeds awaiting_regen/awaiting_caption). `jobs.plan(note=)` /
  `planner.make_plan(note=)` pass the owner's wishes into the plan prompt.
- `app/calendar_india.py` — only high-confidence festival dates (lunar ones marked approx) + owner's custom dates.
- `app/secrets_store.py` — the only code that reads/writes keys. `.env` is loaded with `override=False`, so host
  env vars (Railway/Render) win. `save()` rewrites one line atomically and updates `os.environ` live.
  `scrub()` masks any loaded secret in text; used by the log filter and test-button messages.
- `app/keys.py` — registry of keys + per-service setup guides (SERVICES) shown in the Control Center; `test` names map to functions in `app/key_tests.py`.
- `app/brands.py` — loads `brands/<slug>/config.yaml` + `brand-rules.md`. `Brand.secret(name)` applies the brand's
  `env_prefix` (client brands get `ACME_META_ACCESS_TOKEN` etc.); Claude/Gemini keys fall back to shared ones.
- `app/db.py` — SQLite at `$DATA_DIR/agent.db`; every table has a `brand` column. Tables: products, content_plan,
  drafts, approvals, posts, insights, app_state.
- `app/logging_setup.py` — one log file per day `logs/YYYY-MM-DD.log`, secrets scrubbed; urllib3 kept at WARNING
  because Telegram URLs contain the bot token.
- `app/web/` — Flask "Control Center", the owner's only interface (goal: no file editing, ever). Pages: Home
  (checklist from stored test results, next step, action tiles gated by `current_phase`), Connect (per-service
  guide from `keys.SERVICES` + "Save & test"), Brand (forms parsed in `web/brand_forms.py`), Activity (log viewer).
  First-run password (hash in `.env`), login lockout, CSRF on all POSTs, keys shown masked only.
  When a phase ships, raise `CURRENT_PHASE` in `app/web/__init__.py` and add its action to `jobs.ACTIONS`
  (tiles post to `/action/<key>`). Plan and Products pages read via `planner.upcoming_plan` / `catalog_styles`.
- `app/brand_editor.py` — the only writer of `config.yaml`: ruamel round-trip (keeps comments), quotes strings,
  validates via `load_brand` and rolls back on failure. Also logo upload and Shopify-address normalisation.
- `app/status.py` — last test result per service in `app_state` (`test:<service>`).
- `key_tests.test_meta` auto-discovers the Page (via `/me/accounts`) and IG account and writes them to config.

## Conventions

- Nothing brand-specific in code; it all lives in `brands/<slug>/`.
- Prices, pack sizes, sizes and colours must come only from Shopify data — never generated.
- Never log, display or send (Telegram) a key value. Log key *names* only.
- Look up current external API versions/model names from official docs when building a phase (pinned now:
  Shopify Admin API `2026-10`, Meta Graph `v26.0`). Shopify uses Dev Dashboard client-credentials tokens
  (24h expiry) — legacy `shpat_` tokens are also supported.
- Every new owner-facing setting gets a Control Center form; never ask the owner to edit YAML/.env by hand.
- Keep dependencies minimal; comment code for a non-coder reader.
- Tests never hit the network: `tests/conftest.py` `sandbox` fixture isolates .env/DB/brands; Shopify is faked by
  patching `shopify.graphql` with nodes shaped like the real store's response; Claude by `FakeClaude`.
