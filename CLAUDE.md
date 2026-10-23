# CLAUDE.md

Stock app for RDK (garment manufacturer): production challans (issue to stitchers / receipts), ERP sales, opening stock and physical counts -> closing stock as of any date. Deterministic engine + Streamlit staff UI + "Ask RDAI" chat agent (Claude API) that calls the engine functions as tools - the LLM must never do the stock arithmetic itself. Shipped to the user as a Windows RDAI.exe (no Python needed).

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Seed DB from the transcribed book `data/seed/production_book.csv`: `.venv/bin/python -m rdai.seed` (creates `data/rdai.db`; set `RDAI_DB` to use another path). Adds only challans not already in the DB; the exe runs it on every start, so new book pages are added by appending rows to the CSV.
- Run UI: `.venv/bin/streamlit run app.py`
- Build exe locally: `.venv/bin/pip install pyinstaller && .venv/bin/pyinstaller rdai.spec` (Windows exe is built by `.github/workflows/build-windows.yml`; push a `v*` tag to publish a release zip)
- Tests: `.venv/bin/python -m pytest -q`; single test: `.venv/bin/python -m pytest tests/test_engine.py::test_units -q`

## Architecture

- `rdai/db.py` - SQLite schema. `v_movements` view is the single source of stock: it unions `stock_entries` (OPENING / SALE / ADJUSTMENT) with `receipts` (received -> FRESH full boxes, loose -> LOOSE, addas -> SECOND, unfinished -> UNFINISHED). Closing stock = SUM(qty) where date <= as_of. Schema version in `PRAGMA user_version`; `_migrate` upgrades old databases on connect (v2 split fresh into boxes + loose). The view is rebuilt on every connect.
- `rdai/production.py` - challans, lines, receipts. Line balance: issued = received + addas + short + unfinished + unaccounted. Settling dues = receipt with received +X and short/unfinished -X. Challan kinds: STITCHING (affects stock), SALE_TO_STITCHER and INHOUSE (no stock effect).
- `rdai/stock.py` - entries, file imports (batched, hash-deduplicated, undoable), closing stock, per-SKU ledger.
- `rdai/physical.py` - physical counts, reconcile vs system as of count date, post adjustments.
- `rdai/masters.py` - products + aliases (`alias_key` normalisation), stitchers, size ordering.
- `rdai/io_excel.py` - templates and tolerant column mapping for uploads (ERP exports).
- `rdai/agent.py` - chat assistant: tool schemas, read tools over the engine, `propose_*` write tools that only create proposals (saved only after the user clicks Confirm -> `execute_proposal`), manual tool loop `run_turn` (append-only history). API key + model stored in `app_settings`.
- `app.py` - Streamlit UI only; no business logic there.
- `launcher.py` + `rdai.spec` - RDAI.exe entry: picks data dir next to exe, seeds DB on first run, starts Streamlit, opens browser. New `rdai` modules must be added to `hiddenimports` in `rdai.spec` (app.py's imports are invisible to PyInstaller).

## Conventions

- Fresh stock = FRESH (full boxes, always a multiple of 10 pcs) + LOOSE (loose pieces). A units figure splits as whole units -> boxes, decimal -> loose (50.5 = 500 FRESH + 5 LOOSE) via `units.split_pcs` / `stock.split_qty`. Moving between them is `stock.repack`.
- Store integer pieces only. Convert at the edges with `rdai.units` (units x 10). Reject units with more than 1 decimal.
- Every product name from outside (book, ERP, staff) goes through `find_product` / aliases; unknown names must be mapped by a human, never auto-created on sales import.
- The agent never writes directly: any new data-changing tool must be a `propose_*` tool validated in `validate_proposal` and executed in `execute_proposal`.
- Adjustments always carry a reason; uploads are batches so they can be undone.
