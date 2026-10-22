import pandas as pd
import pytest

from rdai import physical, production as prod, stock
from rdai.db import connect
from rdai.masters import alias_key, find_product, normalize_size
from rdai.seed import load_ledger_csv
from rdai.units import units_to_pcs


@pytest.fixture
def conn():
    return connect(":memory:")


def test_units():
    assert units_to_pcs("7.2") == 72
    assert units_to_pcs(355.0) == 3550
    assert units_to_pcs(None) == 0 and units_to_pcs("") == 0
    with pytest.raises(ValueError):
        units_to_pcs("7.25")


def test_alias_and_size_normalisation():
    assert alias_key("Colin classic trunk 1/E") == alias_key("COLIN  Classic Trunk I/E")
    assert normalize_size("xxl") == "2XL" and normalize_size(85.0) == "85"


def test_stitching_flow_and_stock(conn):
    [line] = prod.create_challan(conn, "100", "2026-10-15", "STITCHING", "Ramesh",
                                 [{"product": "Test Trunk", "size": "90", "issue_units": 10}])
    prod.add_receipt(conn, line, "2026-10-18", received_units=9.5, addas_pcs=2, short_pcs=1, unfinished_pcs=2)
    b = prod.line_balances(conn, line_id=line).iloc[0]
    assert (b.received_pcs, b.unaccounted_pcs, b.owed_by_stitcher_pcs) == (95, 0, 3)
    assert b.status == "Closed - dues pending"

    stock.add_entry(conn, "OPENING", "2026-04-01", "Test Trunk", "90", 40)
    stock.add_entry(conn, "SALE", "2026-10-20", "Test Trunk", "90", 30)
    cs = stock.closing_stock(conn, "2026-10-22").set_index("stock_type")
    assert cs.loc["FRESH", "closing_pcs"] == 40 + 95 - 30
    assert cs.loc["SECOND", "closing_pcs"] == 2
    assert cs.loc["UNFINISHED", "closing_pcs"] == 2
    # as-of date before the receipt excludes it
    assert stock.closing_stock(conn, "2026-10-17", "FRESH").closing_pcs.sum() == 40

    prod.settle_dues(conn, line, "2026-10-21", 2, "UNFINISHED")
    prod.settle_dues(conn, line, "2026-10-21", 1, "SHORT")
    b = prod.line_balances(conn, line_id=line).iloc[0]
    assert (b.owed_by_stitcher_pcs, b.status) == (0, "Closed")
    cs = stock.closing_stock(conn, "2026-10-22").set_index("stock_type")
    assert cs.loc["FRESH", "closing_pcs"] == 108 and cs.loc["UNFINISHED", "closing_pcs"] == 0
    with pytest.raises(ValueError):
        prod.settle_dues(conn, line, "2026-10-21", 1, "SHORT")


def test_non_stitching_challans_do_not_touch_stock(conn):
    [line] = prod.create_challan(conn, "8", "2026-05-01", "SALE_TO_STITCHER", "Kreetika",
                                 [{"product": "C96 Cross SP-43", "size": "M", "issue_units": 20}])
    with pytest.raises(ValueError):
        prod.add_receipt(conn, line, "2026-05-02", received_units=1)
    assert stock.closing_stock(conn, "2026-12-31").empty
    assert prod.pending_with_stitchers(conn).empty


def test_sales_import_rejects_unknown_and_duplicates(conn):
    stock.add_entry(conn, "OPENING", "2026-04-01", "Test Trunk", "90", 100)
    df = pd.DataFrame({"date": ["20-10-2026", "20-10-2026"], "product": ["test trunk", "Mystery"],
                       "size": ["90", "90"], "qty_units": ["2", "1"]})
    rows, errors = stock.prepare_rows(df, "SALE")
    assert not errors and stock.unknown_products(conn, rows) == ["Mystery"]
    with pytest.raises(ValueError):
        stock.import_entries(conn, "SALE", rows, "f.xlsx", "h1")
    rows = [r for r in rows if r["product"] != "Mystery"]
    bid = stock.import_entries(conn, "SALE", rows, "f.xlsx", "h1")
    assert stock.closing_stock(conn, "2026-10-22").closing_pcs.sum() == 80
    with pytest.raises(ValueError):
        stock.import_entries(conn, "SALE", rows, "f.xlsx", "h1")
    stock.delete_batch(conn, bid)
    assert stock.closing_stock(conn, "2026-10-22").closing_pcs.sum() == 100


def test_physical_count_reconcile_and_adjust(conn):
    stock.add_entry(conn, "OPENING", "2026-04-01", "Test Trunk", "90", 100)
    stock.add_entry(conn, "OPENING", "2026-04-01", "Test Trunk", "95", 50)
    cid = physical.save_count(conn, "2026-10-22", [
        dict(product="Test Trunk", size="90", qty_pcs=97, stock_type="FRESH")])
    rec = physical.reconcile(conn, cid)
    assert list(rec.variance_pcs) == [-3]
    assert len(physical.reconcile(conn, cid, include_uncounted=True)) == 2
    assert physical.post_adjustments(conn, cid) == 1
    assert list(physical.reconcile(conn, cid).variance_pcs) == [0]
    with pytest.raises(ValueError):
        physical.post_adjustments(conn, cid)


def test_seed_ledger_loads_and_flags_known_problems(conn):
    assert load_ledger_csv(conn) == 447
    assert len(prod.challan_summary(conn)) == 91
    ex = prod.exceptions(conn).set_index(["challan_no", "size"])
    assert ex.loc[("55", "85"), "unaccounted_pcs"] == 1000
    assert ex.loc[("35", "80"), "unaccounted_pcs"] == 30
    # balanced lines are not flagged
    assert ("3", "100") not in ex.index
    assert find_product(conn, "Active Trunk O/E") is None  # mapped in seed, not a separate product
