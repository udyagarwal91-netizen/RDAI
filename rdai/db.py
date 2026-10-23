"""SQLite schema and connection handling.

All quantities are stored as integer PIECES. Units (1 unit = 10 pieces = 1 box) are only an
input/display format - see rdai.units.

Fresh stock is kept in two parts: FRESH = full boxes (always whole units) and LOOSE = loose
fresh pieces not in a full box. 50.5 units received = 500 pcs FRESH + 5 pcs LOOSE.
"""
import os
import sqlite3

DEFAULT_DB_PATH = os.environ.get("RDAI_DB", os.path.join(os.path.dirname(__file__), "..", "data", "rdai.db"))

STOCK_TYPES = ("FRESH", "LOOSE", "SECOND", "UNFINISHED")
SCHEMA_VERSION = 2
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

-- One row per receiving event. received_pcs = full boxes (x10), loose_pcs = loose fresh pieces.
-- Short may be negative (= extra pieces received).
-- Settling dues later = received +X with short/unfinished -X.
CREATE TABLE IF NOT EXISTS receipts (
    id              INTEGER PRIMARY KEY,
    line_id         INTEGER NOT NULL REFERENCES challan_lines(id) ON DELETE CASCADE,
    receipt_date    TEXT NOT NULL,
    received_pcs    INTEGER NOT NULL DEFAULT 0,
    loose_pcs       INTEGER NOT NULL DEFAULT 0,
    addas_pcs       INTEGER NOT NULL DEFAULT 0,
    short_pcs       INTEGER NOT NULL DEFAULT 0,
    unfinished_pcs  INTEGER NOT NULL DEFAULT 0,
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Opening stock, sales and adjustments. qty_pcs is always positive for SALE
-- (sign is applied in v_movements); ADJUSTMENT carries its own sign.
{stock_entries}

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

{physical_count_lines}

CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE INDEX IF NOT EXISTS ix_receipts_line ON receipts(line_id);
CREATE INDEX IF NOT EXISTS ix_entries_sku ON stock_entries(product_id, size, stock_type);

"""


STOCK_ENTRIES_DDL = """CREATE TABLE IF NOT EXISTS {name} (
    id          INTEGER PRIMARY KEY,
    entry_date  TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('OPENING','SALE','ADJUSTMENT')),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    size        TEXT NOT NULL,
    stock_type  TEXT NOT NULL CHECK (stock_type IN ('FRESH','LOOSE','SECOND','UNFINISHED')),
    qty_pcs     INTEGER NOT NULL,
    ref         TEXT,
    note        TEXT,
    batch_id    INTEGER REFERENCES import_batches(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""

COUNT_LINES_DDL = """CREATE TABLE IF NOT EXISTS {name} (
    count_id    INTEGER NOT NULL REFERENCES physical_counts(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    size        TEXT NOT NULL,
    stock_type  TEXT NOT NULL CHECK (stock_type IN ('FRESH','LOOSE','SECOND','UNFINISHED')),
    qty_pcs     INTEGER NOT NULL,
    PRIMARY KEY (count_id, product_id, size, stock_type)
);"""

SCHEMA = SCHEMA.format(stock_entries=STOCK_ENTRIES_DDL.format(name="stock_entries"),
                       physical_count_lines=COUNT_LINES_DDL.format(name="physical_count_lines"))

VIEW = """
DROP VIEW IF EXISTS v_movements;
-- Every stock movement, signed, in pieces. Closing stock = SUM(qty) up to a date.
CREATE VIEW v_movements AS
    SELECT e.entry_date AS mdate, e.product_id, e.size, e.stock_type,
           CASE e.kind WHEN 'SALE' THEN -e.qty_pcs ELSE e.qty_pcs END AS qty,
           e.kind AS source, COALESCE(e.ref, '') AS ref
      FROM stock_entries e
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'FRESH', r.received_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.received_pcs <> 0
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'LOOSE', r.loose_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.loose_pcs <> 0
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'SECOND', r.addas_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.addas_pcs <> 0
    UNION ALL
    SELECT r.receipt_date, l.product_id, l.size, 'UNFINISHED', r.unfinished_pcs, 'RECEIPT', 'Challan ' || l.challan_no
      FROM receipts r JOIN challan_lines l ON l.id = r.line_id
     WHERE r.unfinished_pcs <> 0;
"""


def _migrate_v1_to_v2(conn):
    """Loose pieces: split every fresh quantity into full boxes (FRESH) + remainder (LOOSE).
    Totals are unchanged - 505 pcs becomes 500 FRESH + 5 LOOSE."""
    conn.execute("ALTER TABLE receipts ADD COLUMN loose_pcs INTEGER NOT NULL DEFAULT 0")
    # SQLite % keeps the sign of the left side, so negative settlements split the same way.
    conn.execute("UPDATE receipts SET loose_pcs = received_pcs % 10, received_pcs = received_pcs - received_pcs % 10")
    for table, ddl, cols in (
        ("stock_entries", STOCK_ENTRIES_DDL,
         "id, entry_date, kind, product_id, size, stock_type, qty_pcs, ref, note, batch_id, created_at"),
        ("physical_count_lines", COUNT_LINES_DDL, "count_id, product_id, size, stock_type, qty_pcs"),
    ):
        conn.execute(ddl.format(name=table + "_v2"))
        conn.execute(f"INSERT INTO {table}_v2 ({cols}) SELECT {cols} FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_v2 RENAME TO {table}")
    loose_cols = "entry_date, kind, product_id, size, stock_type, qty_pcs, ref, note, batch_id"
    conn.execute(f"INSERT INTO stock_entries ({loose_cols}) "
                 "SELECT entry_date, kind, product_id, size, 'LOOSE', qty_pcs % 10, ref, note, batch_id "
                 "FROM stock_entries WHERE stock_type = 'FRESH' AND qty_pcs % 10 <> 0")
    conn.execute("UPDATE stock_entries SET qty_pcs = qty_pcs - qty_pcs % 10 WHERE stock_type = 'FRESH'")
    conn.execute("INSERT INTO physical_count_lines (count_id, product_id, size, stock_type, qty_pcs) "
                 "SELECT count_id, product_id, size, 'LOOSE', qty_pcs % 10 FROM physical_count_lines "
                 "WHERE stock_type = 'FRESH' AND qty_pcs % 10 <> 0")
    conn.execute("UPDATE physical_count_lines SET qty_pcs = qty_pcs - qty_pcs % 10 WHERE stock_type = 'FRESH'")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entries_sku ON stock_entries(product_id, size, stock_type)")


def _migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    receipt_cols = {r["name"] for r in conn.execute("PRAGMA table_info(receipts)")}
    conn.execute("DROP VIEW IF EXISTS v_movements")
    conn.execute("BEGIN")
    try:
        if "loose_pcs" not in receipt_cols:  # database created before loose pieces existed
            _migrate_v1_to_v2(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def connect(path=None):
    path = path or DEFAULT_DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.isolation_level = None  # explicit BEGIN/COMMIT inside _migrate
    _migrate(conn)
    conn.isolation_level = ""
    conn.executescript(VIEW)
    return conn


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(conn, key, value):
    with conn:
        conn.execute("INSERT INTO app_settings(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
