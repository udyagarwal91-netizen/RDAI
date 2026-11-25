"""Bring the app in line with a production-book page (read from a photo by the assistant).

The book is cumulative: a line shows the TOTAL received / addas / short / unfinished so far.
plan_book_update compares each book line with what the app already holds and works out only
what is new - a new challan, a new line, or a receipt for the difference - so re-sending a page
never double-counts. Lines that disagree with the app in a way a receipt can't fix (issue
quantity differs, book shows less than already recorded) are reported, never changed.
apply_book_update re-plans against current data and writes everything in one transaction.
"""
from .masters import find_product, get_or_create_stitcher, normalize_size
from .production import line_balances
from .units import split_pcs, to_pcs, units_to_pcs

KINDS = ("STITCHING", "SALE_TO_STITCHER", "INHOUSE")


def _num(v):
    return 0 if v is None or v == "" else v


def _book_line(raw):
    """Validate one transcribed line -> dict in pieces. Raises ValueError with the line named."""
    label = f"challan {raw.get('challan_no')} {raw.get('product')} {raw.get('size')}"
    try:
        cno = str(raw["challan_no"]).strip()
        if not cno:
            raise ValueError("challan number missing")
        kind = (raw.get("kind") or "STITCHING").upper()
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        return dict(
            challan_no=cno, kind=kind, stitcher=(raw.get("stitcher") or "").strip() or None,
            product_text=str(raw["product"]).strip(), size=normalize_size(raw["size"]),
            issued=units_to_pcs(raw["issue_units"]),
            received=units_to_pcs(_num(raw.get("receive_units"))) + to_pcs(_num(raw.get("loose_pcs"))),
            addas=to_pcs(_num(raw.get("addas_pcs"))), short=to_pcs(_num(raw.get("short_pcs"))),
            unfinished=to_pcs(_num(raw.get("unfinished_pcs"))),
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"{label}: {e}")


def plan_book_update(conn, lines, entry_date):
    """Work out what a book page changes. Returns {"actions", "warnings", "unchanged", "errors"}.

    actions: list of dicts, each one of
      {"type": "new_challan", "challan_no", "kind", "stitcher", "lines": [line...]}
      {"type": "new_line", "challan_no", "line"}
      {"type": "receipt", "challan_no", "line_id", "product", "size", "received", "addas", "short", "unfinished"}
    A line inside new_challan / new_line carries its own receipt totals (received, addas, ...).
    errors: problems that block the whole page (unknown product, bad number) - nothing can be applied.
    """
    errors, warnings, unchanged = [], [], 0
    parsed = []
    for raw in lines:
        try:
            ln = _book_line(raw)
        except ValueError as e:
            errors.append(str(e))
            continue
        pid = find_product(conn, ln["product_text"])
        if pid is None:
            errors.append(f"challan {ln['challan_no']}: unknown product '{ln['product_text']}' - map it to a product "
                          "in the master (find_products) or ask the user whether it is a new product")
            continue
        ln["product_id"] = pid
        ln["product"] = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()["name"]
        parsed.append(ln)
    if errors:
        return {"actions": [], "warnings": warnings, "unchanged": 0, "errors": errors}

    seen = set()
    for ln in parsed:
        key = (ln["challan_no"], ln["product_id"], ln["size"])
        if key in seen:
            errors.append(f"challan {ln['challan_no']} {ln['product']} {ln['size']} appears twice on the page")
        seen.add(key)
        booked = ln["received"] + ln["addas"] + ln["short"] + ln["unfinished"]
        if booked and booked != ln["issued"]:
            gap = ln["issued"] - booked
            hint = ""
            if ln["short"] and ln["short"] * 10 == ln["short"] + gap:
                hint = " (short may be written in units: try short x 10)"
            warnings.append(f"Book line does not balance: challan {ln['challan_no']} {ln['product']} {ln['size']} - "
                            f"issued {ln['issued']}, accounted {booked}, "
                            f"{'missing' if gap > 0 else 'extra'} {abs(gap)} pcs{hint}")
    if errors:
        return {"actions": [], "warnings": warnings, "unchanged": 0, "errors": errors}

    actions = []
    by_challan = {}
    for ln in parsed:
        by_challan.setdefault(ln["challan_no"], []).append(ln)
    for cno, cl in by_challan.items():
        row = conn.execute("SELECT kind FROM challans WHERE challan_no = ?", (cno,)).fetchone()
        if row is None:
            first = cl[0]
            if first["kind"] != "INHOUSE" and not first["stitcher"]:
                warnings.append(f"Challan {cno}: no stitcher name given - it can be filled in later "
                                "(Challans page -> In-house & stitcher)")
            actions.append({"type": "new_challan", "challan_no": cno, "kind": first["kind"],
                            "stitcher": first["stitcher"], "lines": cl})
            continue
        existing = line_balances(conn, challan_no=cno)
        for ln in cl:
            m = existing[(existing["product"] == ln["product"]) & (existing["size"] == ln["size"])]
            if m.empty:
                actions.append({"type": "new_line", "challan_no": cno, "line": ln})
                continue
            cur = m.iloc[0]
            if int(cur["issued_pcs"]) != ln["issued"]:
                warnings.append(f"Challan {cno} {ln['product']} {ln['size']}: issue differs - book {ln['issued']} pcs, "
                                f"app {int(cur['issued_pcs'])} pcs. Not changed; correct the book or the app.")
            delta = {f: ln[f] - int(cur[f"{f}_pcs"]) for f in ("received", "addas", "short", "unfinished")}
            if all(v == 0 for v in delta.values()):
                unchanged += 1
                continue
            if row["kind"] != "STITCHING":
                warnings.append(f"Challan {cno} is {row['kind']}; receipts cannot be recorded against it.")
                continue
            if any(v < 0 for k, v in delta.items() if k != "short"):
                warnings.append(f"Challan {cno} {ln['product']} {ln['size']}: book shows LESS than already recorded "
                                f"({', '.join(f'{k} {v:+d}' for k, v in delta.items() if v)}). Not changed - "
                                "check the book or delete the wrong receipt in the app.")
                continue
            actions.append({"type": "receipt", "challan_no": cno, "line_id": int(cur["line_id"]),
                            "product": ln["product"], "size": ln["size"], **delta})
    return {"actions": actions, "warnings": warnings, "unchanged": unchanged, "errors": errors}


def _receipt_vals(received, addas, short, unfinished):
    boxed, loose = split_pcs(received)
    return boxed, loose, addas, short, unfinished


def _insert_receipt(conn, line_id, date, vals, note):
    if any(vals):
        conn.execute("INSERT INTO receipts(line_id, receipt_date, received_pcs, loose_pcs, addas_pcs, short_pcs, "
                     "unfinished_pcs, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (line_id, date, *vals, note))


def _insert_line(conn, cno, ln, date, note):
    line_id = conn.execute("INSERT INTO challan_lines(challan_no, product_id, size, issued_pcs) VALUES (?, ?, ?, ?)",
                           (cno, ln["product_id"], ln["size"], ln["issued"])).lastrowid
    if ln.get("kind", "STITCHING") == "STITCHING":
        _insert_receipt(conn, line_id, date, _receipt_vals(ln["received"], ln["addas"], ln["short"],
                                                            ln["unfinished"]), note)


def apply_book_update(conn, lines, entry_date, note="From book photo"):
    """Re-plan against current data and apply everything in one transaction. Returns the plan applied."""
    plan = plan_book_update(conn, lines, entry_date)
    if plan["errors"]:
        raise ValueError("; ".join(plan["errors"]))
    if not plan["actions"]:
        raise ValueError("Nothing to change - the app already matches this page")
    with conn:
        for a in plan["actions"]:
            if a["type"] == "new_challan":
                conn.execute("INSERT INTO challans(challan_no, issue_date, kind, stitcher_id) VALUES (?, ?, ?, ?)",
                             (a["challan_no"], entry_date, a["kind"], get_or_create_stitcher(conn, a["stitcher"])))
                for ln in a["lines"]:
                    _insert_line(conn, a["challan_no"], ln, entry_date, note)
            elif a["type"] == "new_line":
                _insert_line(conn, a["challan_no"], a["line"], entry_date, note)
            else:
                _insert_receipt(conn, a["line_id"], entry_date,
                                _receipt_vals(a["received"], a["addas"], a["short"], a["unfinished"]), note)
    return plan


def describe_plan(plan):
    """Markdown summary of a plan for the Confirm box."""
    out = []
    for a in plan["actions"]:
        if a["type"] == "new_challan":
            who = a["stitcher"] or ("RDK in-house" if a["kind"] == "INHOUSE" else "stitcher not given")
            out.append(f"- **New challan {a['challan_no']}** ({a['kind'].lower()}, {who}):")
            for ln in a["lines"]:
                out.append("  - " + _line_text(ln))
        elif a["type"] == "new_line":
            out.append(f"- **Challan {a['challan_no']}, new line:** " + _line_text(a["line"]))
        else:
            out.append(f"- **Challan {a['challan_no']} {a['product']} {a['size']}, received now:** "
                       + _qty_text(a["received"], a["addas"], a["short"], a["unfinished"]))
    if plan["unchanged"]:
        out.append(f"- {plan['unchanged']} line(s) already up to date - no change")
    for w in plan["warnings"]:
        out.append(f"- ⚠️ {w}")
    return "\n".join(out)


def _qty_text(received, addas, short, unfinished):
    boxed, loose = split_pcs(received)
    parts = [f"fresh {boxed // 10} boxes + {loose} loose"] if received else []
    parts += [f"{n} {v} pcs" for n, v in (("addas", addas), ("short", short), ("unfinished", unfinished)) if v]
    return ", ".join(parts) or "nothing"


def _line_text(ln):
    s = f"{ln['product']} {ln['size']}: issued {ln['issued'] / 10:g} units"
    if ln["received"] or ln["addas"] or ln["short"] or ln["unfinished"]:
        s += "; received " + _qty_text(ln["received"], ln["addas"], ln["short"], ln["unfinished"])
    return s
