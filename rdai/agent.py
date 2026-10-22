"""Chat assistant ("Ask RDAI"): Claude answers questions by calling the stock engine.

The model never calculates stock itself - every number comes from a tool that runs the
engine. Tools that would change data do not write anything: they create a *proposal*
that the user must confirm with a button in the app (see execute_proposal).
"""
import datetime as dt
import difflib
import json

import anthropic

from . import physical, production as prod, stock
from .masters import find_product, list_products, normalize_size

MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 (best answers)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (cheaper)",
    "claude-haiku-5-5": "Claude Haiku 5.5 (cheapest)",
}
DEFAULT_MODEL = "claude-opus-5-5"
MAX_ROWS = 300
MAX_TOOL_ROUNDS = 12

SYSTEM_PROMPT = """You are RDAI, the stock assistant for RDK, a garment manufacturer. You help the owner and staff \
know their stock and keep production records up to date.

How the business works:
- Cut pieces are issued to stitchers on numbered issue challans. Stitchers return finished pieces.
- 1 unit = 10 pieces. Issue, receive and ERP sales quantities are in units (one decimal, 7.2 units = 72 pcs). \
Addas (mistake pieces), short and unfinished are in pieces. All tool results are in pieces unless a field says units.
- Received good pieces go to FRESH stock. Addas pieces go to SECOND stock. Unfinished panels go to UNFINISHED stock; \
the stitcher still owes finished pieces for them, and also for short pieces.
- Closing stock = opening stock + received from stitchers - sales +/- adjustments, as of a date.
- Challan types: STITCHING (normal), SALE_TO_STITCHER (a lot sold to a stitcher, no stock effect), \
INHOUSE (written but still in the RDK warehouse, not issued).
- Receipts typed in from the old production book are dated 13-10-2026 because the book had no dates per line.

Rules:
- Never calculate stock totals yourself. Always get numbers from the tools and report them as given. \
You may add up a handful of figures the tool returned when the user asks for a sum, but show the parts.
- Product names must match the product master. If unsure, call find_products first.
- To record or change anything, use a propose_* tool. Nothing is saved until the user clicks Confirm in the app. \
After proposing, tell the user briefly what you proposed and ask them to confirm it with the button. \
Before proposing, make sure you have every value you need; ask the user for anything missing rather than guessing.
- When the user wants to update their records, guide them step by step: new challans issued, goods received, \
sales (large sales files are uploaded on the Sales page), then a physical count on the Physical Count page.
- Keep answers short and practical. Use small tables for stock figures. Quote quantities in pieces and, \
where useful, units in brackets.
"""


# ------------------------------------------------------------------ tool definitions

def _tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": list(required),
                         "additionalProperties": False},
    }


_DATE = {"type": "string", "description": "Date as YYYY-MM-DD"}
_STYPE = {"type": "string", "enum": ["FRESH", "SECOND", "UNFINISHED"],
          "description": "FRESH = good stock, SECOND = addas pieces, UNFINISHED = unfinished panels"}
_PRODUCT = {"type": "string", "description": "Product name from the product master"}
_SIZE = {"type": "string", "description": "Size, e.g. 85, 90, M, XL, 2XL"}

TOOLS = [
    _tool("find_products", "Search the product master by part of a name. Use when unsure of the exact product name.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("get_closing_stock",
          "Closing stock in pieces per product/size as of a date, with opening, received, sold and adjusted. "
          "Leave product empty for all products.",
          {"as_of_date": _DATE, "stock_type": _STYPE, "product": _PRODUCT}, ["as_of_date"]),
    _tool("explain_stock", "Every movement (opening, receipts, sales, adjustments) with running balance for one "
          "product and size. Use to explain how a stock number was reached.",
          {"product": _PRODUCT, "size": _SIZE, "stock_type": _STYPE}, ["product", "size"]),
    _tool("list_challans", "List challans with totals and how many lines are still open.",
          {"stitcher": {"type": "string"}, "only_open": {"type": "boolean"}}),
    _tool("get_challan", "All lines of one challan with issued, received, addas, short, unfinished, unaccounted "
          "and status.", {"challan_no": {"type": "string"}}, ["challan_no"]),
    _tool("pending_with_stitchers", "Lines where a stitcher still owes finished pieces (not received, short or "
          "unfinished). Optionally for one stitcher.", {"stitcher": {"type": "string"}}),
    _tool("get_exceptions", "Book lines that don't balance or were flagged for checking.", {}),
    _tool("get_physical_tally", "Compare a physical stock count with system stock. Without count_id uses the "
          "latest count.", {"count_id": {"type": "integer"}, "only_differences": {"type": "boolean"}}),
    _tool("propose_issue_challan", "Propose a new issue challan. Requires user confirmation in the app.",
          {"challan_no": {"type": "string"}, "issue_date": _DATE,
           "kind": {"type": "string", "enum": ["STITCHING", "SALE_TO_STITCHER", "INHOUSE"]},
           "stitcher": {"type": "string", "description": "Stitcher name (empty only for INHOUSE)"},
           "lines": {"type": "array", "items": {
               "type": "object", "additionalProperties": False, "required": ["product", "size", "issue_units"],
               "properties": {"product": _PRODUCT, "size": _SIZE,
                              "issue_units": {"type": "number", "description": "Units, one decimal"}}}}},
          ["challan_no", "issue_date", "kind", "lines"]),
    _tool("propose_receipt", "Propose recording goods received back against a challan line. Requires user "
          "confirmation.",
          {"challan_no": {"type": "string"}, "product": _PRODUCT, "size": _SIZE, "receipt_date": _DATE,
           "received_units": {"type": "number", "description": "Good pieces received, in UNITS"},
           "addas_pcs": {"type": "integer"},
           "short_pcs": {"type": "integer", "description": "Negative = extra pieces received"},
           "unfinished_pcs": {"type": "integer"}},
          ["challan_no", "product", "size", "receipt_date"]),
    _tool("propose_settle_dues", "Propose recording finished pieces delivered for earlier short or unfinished "
          "pieces on a challan line. Requires user confirmation.",
          {"challan_no": {"type": "string"}, "product": _PRODUCT, "size": _SIZE, "date": _DATE,
           "pieces": {"type": "integer"}, "against": {"type": "string", "enum": ["SHORT", "UNFINISHED"]}},
          ["challan_no", "product", "size", "date", "pieces", "against"]),
    _tool("propose_sale", "Propose a single manual sale entry (bulk sales are uploaded on the Sales page). "
          "Requires user confirmation.",
          {"date": _DATE, "product": _PRODUCT, "size": _SIZE, "qty_units": {"type": "number"},
           "stock_type": _STYPE, "bill_no": {"type": "string"}},
          ["date", "product", "size", "qty_units"]),
    _tool("propose_adjustment", "Propose a stock correction in pieces (+ adds, - removes) with a reason. "
          "Requires user confirmation.",
          {"date": _DATE, "product": _PRODUCT, "size": _SIZE, "stock_type": _STYPE,
           "qty_pcs": {"type": "integer"}, "reason": {"type": "string"}},
          ["date", "product", "size", "stock_type", "qty_pcs", "reason"]),
]


# ------------------------------------------------------------------ read tools

def _records(df, limit=MAX_ROWS):
    out = {"row_count": len(df), "rows": json.loads(df.head(limit).to_json(orient="records"))}
    if len(df) > limit:
        out["note"] = f"Only the first {limit} rows are shown - narrow the question (product or stitcher)."
    return out


def _check_product(conn, name):
    if find_product(conn, name):
        return None
    names = [r["name"] for r in list_products(conn)]
    close = difflib.get_close_matches(name, names, n=5, cutoff=0.4)
    return {"error": f"Unknown product '{name}'.", "did_you_mean": close}


def _line_id(conn, challan_no, product, size):
    lb = prod.line_balances(conn, challan_no=str(challan_no))
    if lb.empty:
        raise ValueError(f"No challan {challan_no}")
    pid = find_product(conn, product)
    names = {r["id"]: r["name"] for r in list_products(conn)}
    m = lb[(lb["product"] == names.get(pid)) & (lb["size"] == normalize_size(size))]
    if m.empty:
        raise ValueError(f"Challan {challan_no} has no line for {product} size {size}. "
                         f"Lines: {sorted(set(zip(lb['product'], lb['size'])))}")
    return int(m.iloc[0]["line_id"]), m.iloc[0]


def run_read_tool(conn, name, args):
    if name == "find_products":
        q = args["query"].lower()
        names = [r["name"] for r in list_products(conn)]
        hits = [n for n in names if q in n.lower()] or difflib.get_close_matches(args["query"], names, n=10, cutoff=0.3)
        return {"products": hits}
    if name == "get_closing_stock":
        if args.get("product") and (err := _check_product(conn, args["product"])):
            return err
        df = stock.closing_stock(conn, args["as_of_date"], args.get("stock_type"), args.get("product") or None)
        res = _records(df.drop(columns=["closing_units"], errors="ignore"))
        if not df.empty:
            res["totals_by_stock_type_pcs"] = df.groupby("stock_type")["closing_pcs"].sum().to_dict()
        return res
    if name == "explain_stock":
        if err := _check_product(conn, args["product"]):
            return err
        return _records(stock.sku_ledger(conn, args["product"], args["size"], args.get("stock_type", "FRESH")))
    if name == "list_challans":
        df = prod.challan_summary(conn)
        if not df.empty and args.get("stitcher"):
            df = df[df["stitcher"].str.lower() == args["stitcher"].lower()]
        if not df.empty and args.get("only_open"):
            df = df[df["open_lines"] > 0]
        return _records(df)
    if name == "get_challan":
        df = prod.line_balances(conn, challan_no=str(args["challan_no"]))
        return _records(df.drop(columns=["line_id"])) if not df.empty else {"error": "No such challan"}
    if name == "pending_with_stitchers":
        df = prod.pending_with_stitchers(conn)
        if not df.empty and args.get("stitcher"):
            df = df[df["stitcher"].str.lower() == args["stitcher"].lower()]
        res = _records(df.drop(columns=["line_id", "receipt_count"], errors="ignore"))
        if not df.empty:
            res["owed_pcs_by_stitcher"] = df.assign(stitcher=df["stitcher"].replace("", "(not assigned)")) \
                .groupby("stitcher")["owed_by_stitcher_pcs"].sum().to_dict()
        return res
    if name == "get_exceptions":
        df = prod.exceptions(conn)
        return _records(df.drop(columns=["line_id", "receipt_count"], errors="ignore"))
    if name == "get_physical_tally":
        counts = physical.list_counts(conn)
        if counts.empty:
            return {"error": "No physical count has been uploaded yet (Physical Count & Tally page)."}
        cid = args.get("count_id") or int(counts.iloc[0]["id"])
        df = physical.reconcile(conn, int(cid))
        if args.get("only_differences", True):
            df = df[df["variance_pcs"] != 0]
        res = _records(df)
        res.update(count_id=cid, count_date=df.attrs.get("count_date"), adjustments_posted=df.attrs.get("adjusted"))
        return res
    raise KeyError(name)


# ------------------------------------------------------------------ proposals (write tools)

def validate_proposal(conn, name, a):
    """Check a proposal against the data and return a human summary. Raises ValueError if invalid."""
    def need_product(p):
        if not find_product(conn, p):
            err = _check_product(conn, p)
            raise ValueError(f"{err['error']} Did you mean: {', '.join(err['did_you_mean']) or 'none'}")

    if name == "propose_issue_challan":
        if conn.execute("SELECT 1 FROM challans WHERE challan_no = ?", (str(a["challan_no"]),)).fetchone():
            raise ValueError(f"Challan {a['challan_no']} already exists")
        if a["kind"] != "INHOUSE" and not a.get("stitcher"):
            raise ValueError("Stitcher name is required")
        if not a["lines"]:
            raise ValueError("At least one line is required")
        for ln in a["lines"]:
            need_product(ln["product"])
        lines = "; ".join(f"{ln['product']} {ln['size']}: {ln['issue_units']} units" for ln in a["lines"])
        return f"New {a['kind']} challan {a['challan_no']} dated {a['issue_date']} to {a.get('stitcher') or 'RDK in-house'} - {lines}"
    if name == "propose_receipt":
        need_product(a["product"])
        _, line = _line_id(conn, a["challan_no"], a["product"], a["size"])
        if line["kind"] != "STITCHING":
            raise ValueError(f"Challan {a['challan_no']} is {line['kind']}; receipts only apply to stitching challans")
        return (f"Receipt on challan {a['challan_no']} {a['product']} {a['size']} dated {a['receipt_date']}: "
                f"received {a.get('received_units', 0)} units, addas {a.get('addas_pcs', 0)} pcs, "
                f"short {a.get('short_pcs', 0)} pcs, unfinished {a.get('unfinished_pcs', 0)} pcs")
    if name == "propose_settle_dues":
        need_product(a["product"])
        _, line = _line_id(conn, a["challan_no"], a["product"], a["size"])
        owed = line["short_pcs"] if a["against"] == "SHORT" else line["unfinished_pcs"]
        if a["pieces"] > owed:
            raise ValueError(f"Only {owed} pcs are outstanding as {a['against'].lower()} on that line")
        return (f"Challan {a['challan_no']} {a['product']} {a['size']}: {a['pieces']} finished pcs received "
                f"against {a['against'].lower()} on {a['date']}")
    if name == "propose_sale":
        need_product(a["product"])
        return (f"Sale on {a['date']}: {a['product']} {a['size']} {a['qty_units']} units "
                f"({a.get('stock_type', 'FRESH')}) bill {a.get('bill_no') or '-'}")
    if name == "propose_adjustment":
        need_product(a["product"])
        if not a.get("reason"):
            raise ValueError("A reason is required")
        return (f"Adjustment on {a['date']}: {a['product']} {a['size']} {a['stock_type']} "
                f"{a['qty_pcs']:+d} pcs - {a['reason']}")
    raise KeyError(name)


def execute_proposal(conn, p):
    """Run a confirmed proposal against the engine. Returns a short result string."""
    name, a = p["tool"], p["args"]
    if name == "propose_issue_challan":
        prod.create_challan(conn, a["challan_no"], a["issue_date"], a["kind"], a.get("stitcher") or None, a["lines"])
    elif name == "propose_receipt":
        lid, _ = _line_id(conn, a["challan_no"], a["product"], a["size"])
        prod.add_receipt(conn, lid, a["receipt_date"], a.get("received_units", 0), a.get("addas_pcs", 0),
                         a.get("short_pcs", 0), a.get("unfinished_pcs", 0), note="Entered via chat")
    elif name == "propose_settle_dues":
        lid, _ = _line_id(conn, a["challan_no"], a["product"], a["size"])
        prod.settle_dues(conn, lid, a["date"], a["pieces"], a["against"])
    elif name == "propose_sale":
        from .units import units_to_pcs
        with conn:
            stock.add_entry(conn, "SALE", a["date"], a["product"], a["size"], units_to_pcs(a["qty_units"]),
                            a.get("stock_type", "FRESH"), ref=a.get("bill_no"), note="Entered via chat",
                            create_product=False)
    elif name == "propose_adjustment":
        stock.add_adjustment(conn, a["date"], a["product"], a["size"], int(a["qty_pcs"]), a["stock_type"], a["reason"])
    else:
        raise KeyError(name)
    return "Saved: " + p["summary"]


# ------------------------------------------------------------------ chat loop

def make_client(api_key):
    return anthropic.Anthropic(api_key=api_key)


def _request_kwargs(model):
    kw = dict(model=model, max_tokens=16000, system=SYSTEM_PROMPT, tools=TOOLS,
              output_config={"effort": "medium"}, cache_control={"type": "ephemeral"})
    if model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        # If Claude declines a request, the API retries it on a fallback model inside the same call.
        kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    return kw


def run_turn(conn, client, history, user_text, proposals, model=DEFAULT_MODEL, today=None):
    """Append the user's message and run the tool loop until Claude answers.

    history: list of message dicts (mutated - append-only, assistant content is kept unchanged).
    proposals: list of proposal dicts (mutated - new proposals appended with status 'pending').
    Returns the assistant's final text.
    """
    today = today or dt.date.today().isoformat()
    history.append({"role": "user", "content": f"[Today is {today}]\n{user_text}"})
    kwargs = _request_kwargs(model)
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.beta.messages.create(messages=history, **kwargs)
        history.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            return "Sorry, I can't help with that request."
        if response.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n(Answer was cut off - ask a narrower question.)"
            return text
        results = []
        for tu in tool_uses:
            try:
                if tu.name.startswith("propose_"):
                    summary = validate_proposal(conn, tu.name, tu.input)
                    pid = len(proposals) + 1
                    proposals.append({"id": pid, "tool": tu.name, "args": dict(tu.input), "summary": summary,
                                      "status": "pending"})
                    content = json.dumps({"proposal_id": pid, "summary": summary,
                                          "status": "Waiting for the user to click Confirm in the app."})
                else:
                    content = json.dumps(run_read_tool(conn, tu.name, tu.input), default=str)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            except (ValueError, KeyError) as e:
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": f"Error: {e}",
                                "is_error": True})
        history.append({"role": "user", "content": results})
    return "I needed too many steps for that. Please ask a more specific question."


def note_decision(history, proposal, result_text):
    """Tell Claude what happened to a proposal on the next turn (append-only)."""
    history.append({"role": "user", "content": f"[App note: proposal #{proposal['id']} was {proposal['status']}. "
                                               f"{result_text}]"})
