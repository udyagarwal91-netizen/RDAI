"""Physical stock counts: upload a count, compare with calculated stock, post adjustments."""
import pandas as pd

from .masters import find_product, size_sort_key
from .stock import closing_stock, add_entry


def save_count(conn, count_date, rows, label=None, file_name=None, data_hash=None):
    """rows: validated dicts from stock.prepare_rows (product, size, qty_pcs, stock_type)."""
    missing = sorted({r["product"] for r in rows if find_product(conn, r["product"]) is None})
    if missing:
        raise ValueError("Unknown products (map them first): " + ", ".join(missing))
    with conn:
        bid = None
        if data_hash:
            if conn.execute("SELECT 1 FROM import_batches WHERE kind='COUNT' AND file_hash=?", (data_hash,)).fetchone():
                raise ValueError("This exact count file has already been uploaded")
            bid = conn.execute("INSERT INTO import_batches(kind, file_name, file_hash, row_count) VALUES ('COUNT', ?, ?, ?)",
                               (file_name, data_hash, len(rows))).lastrowid
        cid = conn.execute("INSERT INTO physical_counts(count_date, label, batch_id) VALUES (?, ?, ?)",
                           (str(count_date), label, bid)).lastrowid
        agg = {}
        for r in rows:
            k = (find_product(conn, r["product"]), r["size"], r["stock_type"])
            agg[k] = agg.get(k, 0) + r["qty_pcs"]
        conn.executemany(
            "INSERT INTO physical_count_lines(count_id, product_id, size, stock_type, qty_pcs) VALUES (?, ?, ?, ?, ?)",
            [(cid, *k, q) for k, q in agg.items()])
    return cid


def list_counts(conn):
    return pd.read_sql_query(
        "SELECT pc.id, pc.count_date, pc.label, pc.adjusted, COUNT(l.product_id) AS lines, SUM(l.qty_pcs) AS total_pcs "
        "FROM physical_counts pc LEFT JOIN physical_count_lines l ON l.count_id = pc.id GROUP BY pc.id ORDER BY pc.id DESC",
        conn)


def reconcile(conn, count_id, include_uncounted=False):
    """Compare a physical count with calculated stock as of the count date.
    include_uncounted: also list items the system has stock for but which were not counted."""
    c = conn.execute("SELECT * FROM physical_counts WHERE id = ?", (count_id,)).fetchone()
    if not c:
        raise ValueError(f"No physical count {count_id}")
    phys = pd.read_sql_query(
        "SELECT p.name AS product, l.size, l.stock_type, l.qty_pcs AS physical_pcs "
        "FROM physical_count_lines l JOIN products p ON p.id = l.product_id WHERE l.count_id = ?",
        conn, params=[count_id])
    calc = closing_stock(conn, c["count_date"])
    calc = calc[["product", "size", "stock_type", "closing_pcs"]] if not calc.empty else \
        pd.DataFrame(columns=["product", "size", "stock_type", "closing_pcs"])
    calc = calc.rename(columns={"closing_pcs": "system_pcs"})
    how = "outer" if include_uncounted else "left"
    df = phys.merge(calc, on=["product", "size", "stock_type"], how=how)
    df["counted"] = df["physical_pcs"].notna()
    df[["physical_pcs", "system_pcs"]] = df[["physical_pcs", "system_pcs"]].fillna(0).astype(int)
    df["variance_pcs"] = df["physical_pcs"] - df["system_pcs"]
    df["result"] = df["variance_pcs"].map(lambda v: "Match" if v == 0 else ("Excess" if v > 0 else "Shortage"))
    df.loc[~df["counted"], "result"] = "Not counted"
    df["_s"] = df["size"].map(size_sort_key)
    df = df.sort_values(["stock_type", "product", "_s"]).drop(columns="_s").reset_index(drop=True)
    df.attrs["count_date"] = c["count_date"]
    df.attrs["adjusted"] = bool(c["adjusted"])
    return df


def post_adjustments(conn, count_id, reason="Physical count"):
    """Make system stock equal the physical count (counted items only). One-time per count."""
    c = conn.execute("SELECT * FROM physical_counts WHERE id = ?", (count_id,)).fetchone()
    if c["adjusted"]:
        raise ValueError("Adjustments for this count were already posted")
    df = reconcile(conn, count_id)
    diffs = df[df["counted"] & (df["variance_pcs"] != 0)]
    with conn:
        for _, r in diffs.iterrows():
            add_entry(conn, "ADJUSTMENT", c["count_date"], r["product"], r["size"], int(r["variance_pcs"]),
                      r["stock_type"], ref=f"COUNT#{count_id}", note=reason, create_product=False)
        conn.execute("UPDATE physical_counts SET adjusted = 1 WHERE id = ?", (count_id,))
    return len(diffs)
