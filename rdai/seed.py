"""Load the transcribed production book (data/seed/production_book.csv) into the database.

Usage:  python -m rdai.seed [--db PATH]
Only challans not already in the database are added, so this is safe to run on every start:
new book pages typed into the CSV reach existing installations, and challans already entered
(or edited) in the app are never touched.

Rows may carry issue_date / receipt_date; blank means the defaults (the first book pages had
no per-line dates, so issues default to 2026-04-01 and receipts to 2026-10-13).
"""
import argparse
import csv
import os

from .db import connect
from .production import add_line
from .masters import get_or_create_stitcher
from .units import split_pcs, to_pcs, units_to_pcs

SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
                         "production_book.csv")


def load_ledger_csv(conn, path=SEED_FILE, issue_date="2026-04-01", receipt_date="2026-10-13"):
    """Add the book's challans that are not in the database yet. Returns the number of lines added."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    existing = {r["challan_no"] for r in conn.execute("SELECT challan_no FROM challans")}
    created, added = set(), 0
    with conn:
        for r in rows:
            cno = r["challan_no"].strip()
            if cno in existing:
                continue
            if cno not in created:
                stitcher = r["stitcher"] if r["kind"] == "SALE_TO_STITCHER" else None
                conn.execute("INSERT INTO challans(challan_no, issue_date, kind, stitcher_id) VALUES (?, ?, ?, ?)",
                             (cno, r.get("issue_date") or issue_date, r["kind"],
                              get_or_create_stitcher(conn, stitcher)))
                created.add(cno)
            line_id = add_line(conn, cno, r["product"], r["size"], units_to_pcs(r["issue_units"]), r["note"])
            vals = (*split_pcs(units_to_pcs(r["receive_units"])), to_pcs(r["addas_pcs"]), to_pcs(r["short_pcs"]),
                    to_pcs(r["unfinished_pcs"]))
            if r["kind"] == "STITCHING" and any(vals):
                conn.execute(
                    "INSERT INTO receipts(line_id, receipt_date, received_pcs, loose_pcs, addas_pcs, short_pcs, "
                    "unfinished_pcs, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'From production book')",
                    (line_id, r.get("receipt_date") or receipt_date, *vals))
            added += 1
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    a = ap.parse_args()
    n = load_ledger_csv(connect(a.db))
    print(f"Added {n} new challan lines")


if __name__ == "__main__":
    main()
