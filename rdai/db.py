"""SQLite schema and connection handling.

All quantities are stored as integer PIECES. Units (1 unit = 10 pieces) are only an
input/display format - see rdai.units.
"""
import os
import sqlite3

DEFAULT_DB_PATH = os.environ.get("RDAI_DB", os.path.join(os.path.dirname(__file__), "..", "data", "rdai.db"))

STOCK_TYPES = ("FRESH", "SECOND", "UNFINISHED")
CHALLAN_KINDS = ("STITCHING", "SALE_TO_STITCHER", "INHOUSE")
ENTRY_KINDS = ("OPENING", "SALE", "ADJUSTMENT")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active  INTEGER NOT NULL DEFAULT 1
);

-- Every spelling we have seen for a product (book, ERP, staff typing) maps to one product.
CREATE TABLE IF NOT EXISTS product_aliases (
    alias_key   TEXT PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stitchers (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE
);

-- STITCHING: cut pieces issued to a stitcher; receipts against it add to stock.
-- SALE_TO_STITCHER: lot sold to a stitcher (e.g. mistake lot); no stock effect.
-- INHOUSE: challan written but goods still in RDK warehouse (not yet issued).
CREATE TABLE IF NOT EXISTS challans (
    challan_no   TEXT PRIMARY KEY,
    issue_date   TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('STITCHING','SALE_TO_STITCHER','INHOUSE')),
    stitcher_id  INTEGER REFERENCES stitchers(id),
    note         TEXT
);

CREATE TABLE IF NOT EXISTS challan_lines (
    id          INTEGER PRIMARY KEY,
    challan_no  TEXT NOT NULL REFERENCES challans(challan_no) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    size        TEXT NOT NULL,
    issued_pcs  INTEGER NOT NULL CHECK (issued_pcs >= 0),
    note        TEXT,
    UNIQUE (challan_no, product_id, size)
);

-- One row per receiving event. Short may be negative (= extra pieces received).
-- Settling dues later = received +X with short/unfinished -X.
CREATE TABLE IF NOT EXISTS receipts (
    id              INTEGER PRIMARY KEY,
    line_id         INTEGER NOT NULL REFERENCES challan_lines(id) ON DELETE CASCADE,
    receipt_date    TEXT NOT NULL,
    received_pcs    INTEGER NOT NULL DEFAULT 0,
    addas_pcs       INTEGER NOT NULL DEFAULT 0,
    short_pcs       INTEGER NOT NULL DEFAULT 0,
    unfinished_pcs  INTEGER NOT NULL DEFAULT 0,
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Opening stock, sales and adjustments. qty_pcs is always positive for SALE
-- (sign is applied in v_movements); ADJUSTMENT carries its own sign.
CREATE TABLE IF NOT EXISTS stock_entries (
    id          INTEGER PRIMARY KEY,
    entry_date  TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('OPENING','SALE','ADJUSTMENT')),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    size        TEXT NOT NULL,
    stock_type  TEXT NOT NULL CHECK (stock_type IN ('FRESH','SECOND','UNFINISHED')),
    qty_pcs     INTEGER NOT NULL,
    ref         TEXT,
    note        TEXT,
    batch_id    INTEGER REFERENCES import_batches(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Each uploaded file is a batch so a wrong upload can be undone in one click,
-- and the same file cannot be imported twice.
CREATE TABLE IF NOT EXISTS import_batches (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    file_name   TEXT,
    file_hash   TEXT,
    row_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (kind, file_hash)
);

CREATE TABLE IF NOT EXISTS physical_counts (
    id          INTEGER PRIMARY KEY,
    count_date  TEXT NOT NULL,
    label       TEXT,
    batch_id    INTEGER REFERENCES import_batches(id),
    adjusted    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS physical_count_lines (
    count_id    INTEGER NOT NULL REFERENCES physical_counts(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    size        TEXT NOT NULL,
    stock_type  TEXT NOT NULL CHECK (stock_type IN ('FRESH','SECOND','UNFINISHED')),
    qty_pcs     INTEGER NOT NULL,
    PRIMARY KEY (count_id, product_id, size, stock_type)
);

CREATE INDEX IF NOT EXISTS ix_receipts_line ON receipts(line_id);
CREATE INDEX IF NOT EXISTS ix_entries_sku ON stock_entries(product_id, size, stock_type);

-- Every stock movement, signed, in pieces. Closing stock = SUM(qty) up to a date.
CREATE VIEW IF NOT EXISTS v_movements AS
    SELECT e.entry_date AS mdate, e.product_id, e.size, e.stock_type,
           CASE e.kind WHEN 'SALE' THEN -e.qty_pcs ELSE e.qty_pcs END AS qty,
           e.kind AS source, COALESCE(e.ref, '') AS ref
      FROM stock_entries e
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'FRESH', r.received_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.received_pcs <> 0
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'SECOND', r.addas_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.addas_pcs <> 0
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'UNFINISHED', r.unfinished_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.unfinished_pcs <> 0;
"""


def connect(path=None):
    path = path or DEFAULT_DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
