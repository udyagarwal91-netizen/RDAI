"""Issue challans to stitchers, record receipts, and report what is pending.

Line balance (all in pieces):
    issued = received + addas + short + unfinished + unaccounted
'unaccounted' should be 0 once the stitcher has fully reported. While a challan is
open it is the quantity still with the stitcher; after closing it is a book error.
"""
import pandas as pd

from .masters import get_or_create_product, get_or_create_stitcher, normalize_size, size_sort_key
from .units import to_pcs, units_to_pcs


def create_challan(conn, challan_no, issue_date, kind, stitcher, lines, note=None):
    """lines: iterable of dicts with product, size, issue_units (or issued_pcs), optional note."""
    challan_no = str(challan_no).strip()
    if not challan_no:
        raise ValueError("Challan number is required")
    if conn.execute("SELECT 1 FROM challans WHERE challan_no = ?", (challan_no,)).fetchone():
        raise ValueError(f"Challan {challan_no} already exists")
    with conn:
        conn.execute(
            "INSERT INTO challans(challan_no, issue_date, kind, stitcher_id, note) VALUES (?, ?, ?, ?, ?)",
            (challan_no, str(issue_date), kind, get_or_create_stitcher(conn, stitcher), note),
        )
        ids = []
        for ln in lines:
            ids.append(add_line(conn, challan_no, ln["product"], ln["size"],
                                ln.get("issued_pcs", None) if "issued_pcs" in ln else units_to_pcs(ln.get("issue_units")),
                                ln.get("note")))
        if not ids:
            raise ValueError("A challan needs at least one line")
    return ids


def add_line(conn, challan_no, product, size, issued_pcs, note=None):
    pid = get_or_create_product(conn, product)
    size = normalize_size(size)
    if not size:
        raise ValueError("Size is required")
    try:
        return conn.execute(
            "INSERT INTO challan_lines(challan_no, product_id, size, issued_pcs, note) VALUES (?, ?, ?, ?, ?)",
            (challan_no, pid, size, int(issued_pcs), note or None),
        ).lastrowid
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ValueError(f"Challan {challan_no} already has a line for {product} size {size}")
        raise


def _line_kind(conn, line_id):
    row = conn.execute(
        "SELECT c.kind FROM challan_lines l JOIN challans c ON c.challan_no = l.challan_no WHERE l.id = ?",
        (line_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No challan line {line_id}")
    return row["kind"]


def add_receipt(conn, line_id, receipt_date, received_units=0, addas_pcs=0, short_pcs=0, unfinished_pcs=0,
                note=None, received_pcs=None):
    """Record goods coming back from the stitcher. received in units (or received_pcs), rest in pieces."""
    kind = _line_kind(conn, line_id)
    if kind != "STITCHING":
        raise ValueError(f"Receipts can only be recorded against STITCHING challans (this one is {kind})")
    rec = received_pcs if received_pcs is not None else units_to_pcs(received_units)
    vals = (rec, to_pcs(addas_pcs), to_pcs(short_pcs), to_pcs(unfinished_pcs))
    if not any(vals):
        raise ValueError("Nothing to record - all quantities are zero")
    with conn:
        return conn.execute(
            "INSERT INTO receipts(line_id, receipt_date, received_pcs, addas_pcs, short_pcs, unfinished_pcs, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (line_id, str(receipt_date), *vals, note or None),
        ).lastrowid


def settle_dues(conn, line_id, receipt_date, pieces, against, note=None):
    """Stitcher delivers finished pieces that were earlier booked as short or unfinished.
    Adds to FRESH stock and reduces the outstanding short/unfinished on the line."""
    pieces = to_pcs(pieces)
    if pieces <= 0:
        raise ValueError("Pieces must be positive")
    if against not in ("SHORT", "UNFINISHED"):
        raise ValueError("against must be SHORT or UNFINISHED")
    bal = line_balances(conn, line_id=line_id).iloc[0]
    outstanding = bal["short_pcs"] if against == "SHORT" else bal["unfinished_pcs"]
    if pieces > outstanding:
        raise ValueError(f"Only {outstanding} pcs are outstanding as {against.lower()} on this line")
    return add_receipt(
        conn, line_id, receipt_date, received_pcs=pieces,
        short_pcs=-pieces if against == "SHORT" else 0,
        unfinished_pcs=-pieces if against == "UNFINISHED" else 0,
        note=note or f"Settled {pieces} pcs {against.lower()}",
    )


def issue_inhouse(conn, challan_no, stitcher, issue_date):
    """An RDK in-house challan is now actually issued to a stitcher."""
    with conn:
        n = conn.execute(
            "UPDATE challans SET kind = 'STITCHING', stitcher_id = ?, issue_date = ? "
            "WHERE challan_no = ? AND kind = 'INHOUSE'",
            (get_or_create_stitcher(conn, stitcher), str(issue_date), str(challan_no)),
        ).rowcount
    if not n:
        raise ValueError(f"Challan {challan_no} is not an in-house challan")


def set_stitcher(conn, challan_nos, stitcher):
    sid = get_or_create_stitcher(conn, stitcher)
    with conn:
        conn.executemany("UPDATE challans SET stitcher_id = ? WHERE challan_no = ?", [(sid, str(c)) for c in challan_nos])


def delete_receipt(conn, receipt_id):
    with conn:
        conn.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))


LINE_SQL = """
SELECT l.id AS line_id, c.challan_no, c.issue_date, c.kind, COALESCE(s.name, '') AS stitcher,
       p.name AS product, l.size, l.issued_pcs,
       COALESCE(SUM(r.received_pcs), 0)   AS received_pcs,
       COALESCE(SUM(r.addas_pcs), 0)      AS addas_pcs,
       COALESCE(SUM(r.short_pcs), 0)      AS short_pcs,
       COALESCE(SUM(r.unfinished_pcs), 0) AS unfinished_pcs,
       COUNT(r.id) AS receipt_count,
       MAX(r.receipt_date) AS last_receipt,
       COALESCE(l.note, '') AS note
  FROM challan_lines l
  JOIN challans c ON c.challan_no = l.challan_no
  JOIN products p ON p.id = l.product_id
  LEFT JOIN stitchers s ON s.id = c.stitcher_id
  LEFT JOIN receipts r ON r.line_id = l.id
 WHERE 1 = 1 {where}
 GROUP BY l.id
"""


def _status(row):
    if row["kind"] == "INHOUSE":
        return "In-house (not issued)"
    if row["kind"] == "SALE_TO_STITCHER":
        return "Sold to stitcher"
    if row["receipt_count"] == 0:
        return "Not received"
    if row["unaccounted_pcs"] > 0:
        return "Partly received / unaccounted"
    if row["unaccounted_pcs"] < 0:
        return "Over-booked (check)"
    if row["short_pcs"] > 0 or row["unfinished_pcs"] > 0:
        return "Closed - dues pending"
    if row["short_pcs"] < 0:
        return "Closed - extra received"
    return "Closed"


def line_balances(conn, line_id=None, challan_no=None, stitcher=None):
    where, params = "", []
    if line_id is not None:
        where += " AND l.id = ?"; params.append(line_id)
    if challan_no is not None:
        where += " AND c.challan_no = ?"; params.append(str(challan_no))
    if stitcher is not None:
        where += " AND s.name = ?"; params.append(stitcher)
    df = pd.read_sql_query(LINE_SQL.format(where=where), conn, params=params)
    if df.empty:
        df["unaccounted_pcs"] = []
        df["owed_by_stitcher_pcs"] = []
        df["status"] = []
        return df
    df["unaccounted_pcs"] = df["issued_pcs"] - df["received_pcs"] - df["addas_pcs"] - df["short_pcs"] - df["unfinished_pcs"]
    # Finished pieces the stitcher still has to deliver: unaccounted + short + unfinished.
    df["owed_by_stitcher_pcs"] = df["issued_pcs"] - df["received_pcs"] - df["addas_pcs"]
    stitching = df["kind"] == "STITCHING"
    df.loc[~stitching, ["unaccounted_pcs", "owed_by_stitcher_pcs"]] = 0
    df["status"] = df.apply(_status, axis=1)
    df["_c"] = pd.to_numeric(df["challan_no"], errors="coerce")
    df["_s"] = df["size"].map(size_sort_key)
    return df.sort_values(["_c", "challan_no", "product", "_s"]).drop(columns=["_c", "_s"]).reset_index(drop=True)


def challan_summary(conn):
    df = line_balances(conn)
    if df.empty:
        return df
    g = df.groupby(["challan_no", "issue_date", "kind", "stitcher"], sort=False).agg(
        products=("product", lambda s: ", ".join(dict.fromkeys(s))),
        issued_pcs=("issued_pcs", "sum"), received_pcs=("received_pcs", "sum"),
        addas_pcs=("addas_pcs", "sum"), short_pcs=("short_pcs", "sum"),
        unfinished_pcs=("unfinished_pcs", "sum"), unaccounted_pcs=("unaccounted_pcs", "sum"),
        owed_by_stitcher_pcs=("owed_by_stitcher_pcs", "sum"),
        open_lines=("status", lambda s: int((s != "Closed").sum())),
    ).reset_index()
    return g


def pending_with_stitchers(conn):
    """Lines where the stitcher still owes finished pieces (open or with short/unfinished dues)."""
    df = line_balances(conn)
    if df.empty:
        return df
    return df[(df["kind"] == "STITCHING") & (df["owed_by_stitcher_pcs"] != 0)].reset_index(drop=True)


def exceptions(conn):
    """Lines that need a human look: book doesn't balance after receiving, or a review note exists."""
    df = line_balances(conn)
    if df.empty:
        return df
    received = (df["kind"] == "STITCHING") & (df["receipt_count"] > 0)
    imbalance = received & (df["unaccounted_pcs"] != 0)
    noted = df["note"].str.contains("CHECK|confirm|whited", case=False, regex=True)
    out = df[imbalance | noted].copy()
    out["issue"] = ""
    out.loc[imbalance[imbalance | noted], "issue"] = out["unaccounted_pcs"].map(
        lambda u: f"{u} pcs not accounted for" if u > 0 else f"{-u} pcs booked more than issued")
    return out.reset_index(drop=True)
