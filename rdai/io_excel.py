"""Excel/CSV templates and tolerant reading of uploaded files (staff and ERP exports)."""
import io
import re

import pandas as pd

TEMPLATES = {
    "opening_stock": ["Date", "Product", "Size", "Stock Type", "Qty Units", "Qty Pieces"],
    "sales": ["Date", "Product", "Size", "Stock Type", "Qty Units", "Qty Pieces", "Bill No"],
    "physical_count": ["Product", "Size", "Stock Type", "Qty Units", "Qty Pieces"],
}

TEMPLATE_HELP = (
    "Quantity = Qty Units x 10 + Qty Pieces. Fill either column (or both, e.g. 7 units + 3 pcs). "
    "Stock Type is FRESH (default if blank), SECOND (addas) or UNFINISHED (panels). "
    "Dates as DD-MM-YYYY."
)

# Header spellings we accept -> internal column name
SYNONYMS = {
    "date": "date", "bill date": "date", "invoice date": "date", "voucher date": "date",
    "product": "product", "item": "product", "item name": "product", "product name": "product",
    "items name": "product", "style": "product", "description": "product",
    "size": "size",
    "stock type": "stock_type", "type": "stock_type",
    "qty units": "qty_units", "units": "qty_units", "qty": "qty_units", "quantity": "qty_units",
    "qty (units)": "qty_units",
    "qty pieces": "qty_pcs", "pieces": "qty_pcs", "pcs": "qty_pcs", "qty pcs": "qty_pcs",
    "bill no": "ref", "bill": "ref", "invoice": "ref", "invoice no": "ref", "voucher no": "ref", "ref": "ref",
}


def template_bytes(name):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(columns=TEMPLATES[name]).to_excel(xw, index=False, sheet_name=name)
        pd.DataFrame({"Notes": [TEMPLATE_HELP]}).to_excel(xw, index=False, sheet_name="help")
    return buf.getvalue()


def read_table(file_name, data: bytes):
    if file_name.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(data), dtype=object)
    return pd.read_csv(io.BytesIO(data), dtype=object)


def guess_mapping(columns):
    """{original column: internal name} for headers we recognise."""
    out = {}
    for c in columns:
        key = re.sub(r"\s+", " ", str(c).strip().lower())
        if key in SYNONYMS and SYNONYMS[key] not in out.values():
            out[c] = SYNONYMS[key]
    return out


def apply_mapping(df, mapping):
    """mapping: {original column: internal name}; unmapped columns are dropped."""
    keep = {k: v for k, v in mapping.items() if v}
    return df[list(keep)].rename(columns=keep)


def to_excel_bytes(sheets: dict):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=not isinstance(df.index, pd.RangeIndex))
    return buf.getvalue()
