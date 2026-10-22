"""Product, alias and stitcher masters, plus size ordering."""
import re

LETTER_SIZES = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]


def alias_key(name):
    """Normalise a product name so trivial spelling differences match.
    'Colin classic trunk 1/E' and 'COLIN  Classic Trunk I/E' give the same key."""
    k = str(name).strip().lower()
    k = re.sub(r"\s+", " ", k)
    k = re.sub(r"\b[1l]/e\b", "i/e", k)   # book writes I/E as 1/E or l/E
    k = re.sub(r"\b0/e\b", "o/e", k)
    k = k.replace(".", "")
    return k


def normalize_size(size):
    s = str(size).strip().upper().replace(" ", "")
    if s.endswith(".0") and s[:-2].isdigit():  # Excel turns 85 into 85.0
        s = s[:-2]
    s = {"XXL": "2XL", "XXXL": "3XL", "XXXXL": "4XL"}.get(s, s)
    return s


def size_sort_key(size):
    s = normalize_size(size)
    if s.isdigit():
        return (0, int(s), s)
    if s in LETTER_SIZES:
        return (1, LETTER_SIZES.index(s), s)
    return (2, 0, s)


def find_product(conn, name):
    """Return product id for a name or any known alias, else None."""
    row = conn.execute("SELECT product_id FROM product_aliases WHERE alias_key = ?", (alias_key(name),)).fetchone()
    return row["product_id"] if row else None


def get_or_create_product(conn, name):
    pid = find_product(conn, name)
    if pid:
        return pid
    name = re.sub(r"\s+", " ", str(name).strip())
    cur = conn.execute("INSERT INTO products(name) VALUES (?)", (name,))
    pid = cur.lastrowid
    conn.execute("INSERT OR IGNORE INTO product_aliases(alias_key, product_id) VALUES (?, ?)", (alias_key(name), pid))
    return pid


def add_alias(conn, alias, product_id):
    conn.execute(
        "INSERT INTO product_aliases(alias_key, product_id) VALUES (?, ?) "
        "ON CONFLICT(alias_key) DO UPDATE SET product_id = excluded.product_id",
        (alias_key(alias), product_id),
    )


def rename_product(conn, product_id, new_name):
    conn.execute("UPDATE products SET name = ? WHERE id = ?", (new_name.strip(), product_id))
    add_alias(conn, new_name, product_id)


def merge_products(conn, keep_id, drop_id):
    """Move everything from drop_id onto keep_id (fixes duplicate spellings)."""
    if keep_id == drop_id:
        return
    with conn:
        for table in ("challan_lines", "stock_entries", "physical_count_lines"):
            conn.execute(f"UPDATE OR ABORT {table} SET product_id = ? WHERE product_id = ?", (keep_id, drop_id))
        conn.execute("UPDATE product_aliases SET product_id = ? WHERE product_id = ?", (keep_id, drop_id))
        conn.execute("DELETE FROM products WHERE id = ?", (drop_id,))


def list_products(conn):
    return conn.execute("SELECT id, name FROM products WHERE active = 1 ORDER BY name").fetchall()


def get_or_create_stitcher(conn, name):
    if name is None or not str(name).strip():
        return None
    name = str(name).strip()
    row = conn.execute("SELECT id FROM stitchers WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO stitchers(name) VALUES (?)", (name,)).lastrowid


def list_stitchers(conn):
    return conn.execute("SELECT id, name FROM stitchers ORDER BY name").fetchall()
