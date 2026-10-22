"""Opening stock, sales, adjustments and the closing-stock calculation.

Closing stock (per product, size, stock type) as of a date =
    opening + received from stitchers - sales +/- adjustments
where FRESH gets received good pieces, SECOND gets addas pieces and UNFINISHED
gets unfinished panels.
"""
import hashlib

import pandas as pd

from .db import STOCK_TYPES
from .masters import find_product, get_or_create_product, normalize_size, size_sort_key
from .units import to_pcs, units_to_pcs


def _check_type(stock_type):
    st = str(stock_type or "FRESH").strip().upper()
    st = {"ADDAS": "SECOND", "SECONDS": "SECOND", "UNFINISHED PANELS": "UNFINISHED"}.get(st, st)
    if st not in STOCK_TYPES:
        raise ValueError(f"Stock type must be one of {', '.join(STOCK_TYPES)}, got {stock_type!r}")
    return st


def add_entry(conn, kind, entry_date, product, size, qty_pcs, stock_type="FRESH", ref=None, note=None,
              batch_id=None, create_product=True):
    pid = get_or_create_product(conn, product) if create_product else find_product(conn, product)
    if pid is None:
        raise ValueError(f"Unknown product {product!r}")
    if kind in ("OPENING", "SALE") and qty_pcs < 0:
        raise ValueError(f"{kind} quantity cannot be negative")
    return conn.execute(
        "INSERT INTO stock_entries(entry_date, kind, product_id, size, stock_type, qty_pcs, ref, note, batch_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(entry_date), kind, pid, normalize_size(size), _check_type(stock_type), int(qty_pcs), ref, note, batch_id),
    ).lastrowid


def add_adjustment(conn, entry_date, product, size, qty_pcs, stock_type, reason, ref=None):
    if not qty_pcs:
        raise ValueError("Adjustment quantity cannot be zero")
    if not reason:
        raise ValueError("A reason is required for every adjustment")
    with conn:
        return add_entry(conn, "ADJUSTMENT", entry_date, product, size, qty_pcs, stock_type, ref=ref, note=reason)


# ---------------------------------------------------------------- file imports

def file_hash(data: bytes):
    return hashlib.sha256(data).hexdigest()


def _qty_from_row(row):
    """Quantity in pieces = Qty Units x 10 + Qty Pieces (either column may be blank)."""
    return units_to_pcs(row.get("qty_units")) + to_pcs(row.get("qty_pcs"))


def prepare_rows(df, kind, default_date=None):
    """Validate a normalised DataFrame (columns: date, product, size, qty_units/qty_pcs, stock_type, ref).
    Returns (rows, errors). Nothing is written."""
    rows, errors = [], []
    for i, r in df.iterrows():
        line = i + 2  # header is row 1 in Excel
        try:
            d = r.get("date") if "date" in df.columns else None
            if d is None or (isinstance(d, float) and d != d) or str(d).strip() == "":
                if not default_date:
                    raise ValueError("date is missing")
                d = default_date
            d = pd.to_datetime(d, dayfirst=True).date().isoformat()
            product = str(r.get("product", "")).strip()
            if not product or product.lower() == "nan":
                raise ValueError("product is missing")
            size = normalize_size(r.get("size", ""))
            if not size or size == "NAN":
                raise ValueError("size is missing")
            qty = _qty_from_row(r)
            if qty == 0:
                continue
            rows.append(dict(date=d, product=product, size=size, qty_pcs=qty,
                             stock_type=_check_type(r.get("stock_type") if isinstance(r.get("stock_type"), str) else "FRESH"),
                             ref=None if pd.isna(r.get("ref", None)) else str(r.get("ref"))))
        except ValueError as e:
            errors.append(f"Row {line}: {e}")
    return rows, errors


def unknown_products(conn, rows):
    return sorted({r["product"] for r in rows if find_product(conn, r["product"]) is None})


def import_entries(conn, kind, rows, file_name, data_hash, create_products=False):
    """Write validated rows as one batch. Unknown products must be mapped first unless create_products."""
    if kind not in ("OPENING", "SALE"):
        raise ValueError("kind must be OPENING or SALE")
    if not create_products:
        unknown = unknown_products(conn, rows)
        if unknown:
            raise ValueError("Unknown products (map them first): " + ", ".join(unknown))
    if conn.execute("SELECT 1 FROM import_batches WHERE kind = ? AND file_hash = ?", (kind, data_hash)).fetchone():
        raise ValueError("This exact file has already been imported")
    with conn:
        bid = conn.execute("INSERT INTO import_batches(kind, file_name, file_hash, row_count) VALUES (?, ?, ?, ?)",
                           (kind, file_name, data_hash, len(rows))).lastrowid
        for r in rows:
            add_entry(conn, kind, r["date"], r["product"], r["size"], r["qty_pcs"], r["stock_type"],
                      ref=r.get("ref"), batch_id=bid, create_product=create_products)
    return bid


def list_batches(conn, kind=None):
    q = "SELECT id, kind, file_name, row_count, created_at FROM import_batches"
    return pd.read_sql_query(q + (" WHERE kind = ?" if kind else "") + " ORDER BY id DESC", conn,
                             params=[kind] if kind else [])


def delete_batch(conn, batch_id):
    """Undo a whole upload."""
    with conn:
        conn.execute("DELETE FROM stock_entries WHERE batch_id = ?", (batch_id,))
        conn.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))


# ---------------------------------------------------------------- stock report

STOCK_SQL = """
SELECT p.name AS product, m.size, m.stock_type,
       SUM(CASE WHEN m.source = 'OPENING'    THEN m.qty ELSE 0 END) AS opening_pcs,
       SUM(CASE WHEN m.source = 'RECEIPT'    THEN m.qty ELSE 0 END) AS received_pcs,
       -SUM(CASE WHEN m.source = 'SALE'      THEN m.qty ELSE 0 END) AS sold_pcs,
       SUM(CASE WHEN m.source = 'ADJUSTMENT' THEN m.qty ELSE 0 END) AS adjusted_pcs,
       SUM(m.qty) AS closing_pcs
  FROM v_movements m JOIN products p ON p.id = m.product_id
 WHERE m.mdate <= ? {where}
 GROUP BY p.name, m.size, m.stock_type
"""


def closing_stock(conn, as_of, stock_type=None, product=None):
    """Closing stock as of a date (inclusive). One row per product/size/stock type."""
    where, params = "", [str(as_of)]
    if stock_type:
        where += " AND m.stock_type = ?"; params.append(_check_type(stock_type))
    if product:
        pid = find_product(conn, product)
        if pid is None:
            raise ValueError(f"Unknown product {product!r}")
        where += " AND m.product_id = ?"; params.append(pid)
    df = pd.read_sql_query(STOCK_SQL.format(where=where), conn, params=params)
    if df.empty:
        return df
    df["closing_units"] = df["closing_pcs"] / 10
    df["_s"] = df["size"].map(size_sort_key)
    return df.sort_values(["stock_type", "product", "_s"]).drop(columns="_s").reset_index(drop=True)


def stock_matrix(df, value="closing_pcs"):
    """Pivot a closing_stock frame to product x size for one stock type."""
    if df.empty:
        return df
    m = df.pivot_table(index="product", columns="size", values=value, aggfunc="sum")
    m = m[sorted(m.columns, key=size_sort_key)]
    m["TOTAL"] = m.sum(axis=1)
    return m


def sku_ledger(conn, product, size, stock_type="FRESH"):
    """Every movement for one product/size with running balance - for explaining a number."""
    pid = find_product(conn, product)
    if pid is None:
        raise ValueError(f"Unknown product {product!r}")
    df = pd.read_sql_query(
        "SELECT mdate AS date, source, ref, qty AS qty_pcs FROM v_movements "
        "WHERE product_id = ? AND size = ? AND stock_type = ? ORDER BY mdate, source",
        conn, params=[pid, normalize_size(size), _check_type(stock_type)])
    df["balance_pcs"] = df["qty_pcs"].cumsum()
    return df
