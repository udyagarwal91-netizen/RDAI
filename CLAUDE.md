# CLAUDE.md

Stock app for RDK (garment manufacturer): production challans (issue to stitchers / receipts), ERP sales, opening stock and physical counts -> closing stock as of any date. Phase 1 = deterministic engine + Streamlit staff UI. Phase 2 (planned) = chat agent that calls the engine functions as tools - the LLM must never do the stock arithmetic itself.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Seed DB from the transcribed book: `.venv/bin/python -m rdai.seed` (creates `data/rdai.db`; set `RDAI_DB` to use another path)
- Run UI: `.venv/bin/streamlit run app.py`
- Tests: `.venv/bin/python -m pytest -q`; single test: `.venv/bin/python -m pytest tests/test_engine.py::test_units -q`

## Architecture

- `rdai/db.py` - SQLite schema. `v_movements` view is the single source of stock: it unions `stock_entries` (OPENING / SALE / ADJUSTMENT) with `receipts` (received -> FRESH, addas -> SECOND, unfinished -> UNFINISHED). Closing stock = SUM(qty) where date <= as_of.
- `rdai/production.py` - challans, lines, receipts. Line balance: issued = received + addas + short + unfinished + unaccounted. Settling dues = receipt with received +X and short/unfinished -X. Challan kinds: STITCHING (affects stock), SALE_TO_STITCHER and INHOUSE (no stock effect).
- `rdai/stock.py` - entries, file imports (batched, hash-deduplicated, undoable), closing stock, per-SKU ledger.
- `rdai/physical.py` - physical counts, reconcile vs system as of count date, post adjustments.
- `rdai/masters.py` - products + aliases (`alias_key` normalisation), stitchers, size ordering.
- `rdai/io_excel.py` - templates and tolerant column mapping for uploads (ERP exports).
- `app.py` - Streamlit UI only; no business logic there.

## Conventions

- Store integer pieces only. Convert at the edges with `rdai.units` (units x 10). Reject units with more than 1 decimal.
- Every product name from outside (book, ERP, staff) goes through `find_product` / aliases; unknown names must be mapped by a human, never auto-created on sales import.
- Adjustments always carry a reason; uploads are batches so they can be undone.
