# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A social media content agent for the innerwear brand Skipper Packs (and, later, client garment brands):
Shopify products → weekly plan (Claude) → images/reels (HTML templates via Playwright, Gemini images, Veo reels)
→ quality/price/safety checks → Telegram approval → Meta Graph API publishing → weekly insights fed back into planning.
The owner is not a coder: user-facing text, docs and error messages must be plain English with exact click paths.

Build roadmap (one phase at a time, confirm with the owner after each): 0 setup ✅ → 1 Shopify sync + weekly plan
on Telegram → 2 captions/images/carousels/Gemini images + QC + approval → 2b Veo reels → 3 publishing → 4 insights
→ 5 extras. The full brief is the owner's original prompt; key decisions so far: laptop-only hosting for now
(deploy-ready for Railway), captions in English with light Hinglish, max 4 reels/week.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py                     # Settings page at http://localhost:8000 (NO_BROWSER=1 to skip opening a browser)
.venv/bin/python -m pytest -q               # all tests
.venv/bin/python -m pytest -q tests/test_phase0.py::test_mask   # single test
```

To run without touching the real `.env`/data, set `ENV_FILE`, `DATA_DIR`, `LOGS_DIR` to a scratch location.

## Architecture

- `run.py` — single entry point; loads `.env`, sets up logging + DB, validates brands, serves the Flask Settings
  page. Later phases add APScheduler jobs and the Telegram bot to this same process.
- `app/secrets_store.py` — the only code that reads/writes keys. `.env` is loaded with `override=False`, so host
  env vars (Railway/Render) win. `save()` rewrites one line atomically and updates `os.environ` live.
  `scrub()` masks any loaded secret in text; used by the log filter and test-button messages.
- `app/keys.py` — registry of keys shown on the Settings page; `test` names map to functions in `app/key_tests.py`.
- `app/brands.py` — loads `brands/<slug>/config.yaml` + `brand-rules.md`. `Brand.secret(name)` applies the brand's
  `env_prefix` (client brands get `ACME_META_ACCESS_TOKEN` etc.); Claude/Gemini keys fall back to shared ones.
- `app/db.py` — SQLite at `$DATA_DIR/agent.db`; every table has a `brand` column. Tables: products, content_plan,
  drafts, approvals, posts, insights, app_state.
- `app/logging_setup.py` — one log file per day `logs/YYYY-MM-DD.log`, secrets scrubbed; urllib3 kept at WARNING
  because Telegram URLs contain the bot token.
- `app/web/` — Flask Settings page: first-run password creation (hash stored in `.env`), login lockout, CSRF on all
  POSTs, keys shown masked only.

## Conventions

- Nothing brand-specific in code; it all lives in `brands/<slug>/`.
- Prices, pack sizes, sizes and colours must come only from Shopify data — never generated.
- Never log, display or send (Telegram) a key value. Log key *names* only.
- Look up current external API versions/model names from official docs when building a phase (pinned now:
  Shopify Admin API `2026-10`, Meta Graph `v26.0`). Shopify uses Dev Dashboard client-credentials tokens
  (24h expiry) — legacy `shpat_` tokens are also supported.
- Keep dependencies minimal; comment code for a non-coder reader.
