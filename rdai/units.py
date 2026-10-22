"""Units <-> pieces. 1 unit = 10 pieces. Issue/receive/sales/ERP figures are in units
(one decimal place, e.g. 7.2 units = 72 pcs); addas/short/unfinished are in pieces."""
from decimal import Decimal, InvalidOperation

PCS_PER_UNIT = 10


def _blank(v):
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    return isinstance(v, str) and not v.strip()


def units_to_pcs(units):
    """7.2 -> 72. Blank -> 0. Rejects values that aren't whole pieces (e.g. 7.25)."""
    if _blank(units):
        return 0
    try:
        d = Decimal(str(units).strip()) * PCS_PER_UNIT
    except InvalidOperation:
        raise ValueError(f"Not a number: {units!r}")
    if d != d.to_integral_value():
        raise ValueError(f"{units} units is not a whole number of pieces (max 1 decimal place)")
    return int(d)


def to_pcs(value):
    """Parse a pieces figure. Blank -> 0."""
    if _blank(value):
        return 0
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"Not a number: {value!r}")
    if d != d.to_integral_value():
        raise ValueError(f"Pieces must be a whole number, got {value}")
    return int(d)


def pcs_to_units(pcs):
    return pcs / PCS_PER_UNIT


def fmt_units(pcs):
    """72 -> '7.2'."""
    return f"{pcs / PCS_PER_UNIT:.1f}"
