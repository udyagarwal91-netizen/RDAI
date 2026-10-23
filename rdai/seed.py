"""Load the transcribed production book (data/seed/*.csv) into the database.

Usage:  python -m rdai.seed [--db PATH] [--issue-date 2026-04-01] [--receipt-date 2026-10-13]
The book has no per-line dates, so all issues get --issue-date and all receipts --receipt-date.
"""
import argparse
import csv
import os

from .db import connect
from .production import add_line
from .masters import get_or_create_stitcher
from .units import split_pcs, to_pcs, units_to_pcs

SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed",
                         "ledger_2026-04-01_to_2026-10-13.csv")


def load_ledger_csv(conn, path=SEED_FILE, issue_date="2026-04-01", receipt_date="2026-10-13"):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    created = set()
    with conn:
        for r in rows:
            cno = r["challan_no"].strip()
            if cno not in created:
                if conn.execute("SELECT 1 FROM challans WHERE challan_no = ?", (cno,)).fetchone():
                    raise ValueError(f"Challan {cno} already in database - seed only into an empty database")
                stitcher = r["stitcher"] if r["kind"] == "SALE_TO_STITCHER" else None
                conn.execute("INSERT INTO challans(challan_no, issue_date, kind, stitcher_id) VALUES (?, ?, ?, ?)",
                             (cno, issue_date, r["kind"], get_or_create_stitcher(conn, stitcher)))
                created.add(cno)
            line_id = add_line(conn, cno, r["product"], r["size"], units_to_pcs(r["issue_units"]), r["note"])
            vals = (*split_pcs(units_to_pcs(r["receive_units"])), to_pcs(r["addas_pcs"]), to_pcs(r["short_pcs"]),
                    to_pcs(r["unfinished_pcs"]))
            if r["kind"] == "STITCHING" and any(vals):
                conn.execute(
                    "INSERT INTO receipts(line_id, receipt_date, received_pcs, loose_pcs, addas_pcs, short_pcs, "
                    "unfinished_pcs, note) VALUES (?, ?, ?, ?, ?, ?, ?, 'From production book')",
                    (line_id, receipt_date, *vals))
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--issue-date", default="2026-04-01")
    ap.add_argument("--receipt-date", default="2026-10-13")
    a = ap.parse_args()
    conn = connect(a.db)
    n = load_ledger_csv(conn, issue_date=a.issue_date, receipt_date=a.receipt_date)
    print(f"Loaded {n} challan lines")


if __name__ == "__main__":
    main()
