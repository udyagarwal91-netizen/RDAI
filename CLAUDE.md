# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A social media content agent for the innerwear brand Skipper Packs (and, later, client garment brands):
Shopify products → weekly plan (Claude) → images/reels (HTML templates via Playwright, Gemini images, Veo reels)
→ quality/price/safety checks → Telegram approval → Meta Graph API publishing → weekly insights fed back into planning.
The owner is not a coder: user-facing text, docs and error messages must be plain English with exact click paths.

Build roadmap (one phase at a time, confirm with the owner after each): 0 setup ✅ → 1 Shopify sync + weekly plan
on Telegram ✅ (awaiting owner's live test) → 2 captions/images/carousels/Gemini images + QC + approval → 2b Veo reels → 3 publishing → 4 insights
→ 5 extras. The full brief is the owner's original prompt; key decisions so far: laptop-only hosting for now
(deploy-ready for Railway), captions in English with light Hinglish, max 4 reels/week.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py                     # Control Center at http://localhost:8000 (NO_BROWSER=1 to skip opening a browser)
.venv/bin/python -m pytest -q               # all tests
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
- `app/jobs.py` — the single place actions live (`sync`, `plan`, `send_plan`); used by Control Center buttons
  (background threads, status in `app_state` `job:<action>`) and the timer (weekly plan, daily 06:15 sync).
- `app/telegram.py` — HTML messages split under 4096 chars; `Bot` long-polls `getUpdates` and ignores every chat
  except `TELEGRAM_CHAT_ID` (it idles until that is set, so the Settings test can still discover the chat ID).
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
