"""RDAI stock app - staff screen.  Run:  streamlit run app.py"""
import datetime as dt
import os

import pandas as pd
import streamlit as st

from rdai import agent, physical, production as prod, stock
from rdai.db import CHALLAN_KINDS, STOCK_TYPES, connect, get_setting, set_setting
from rdai.io_excel import (TEMPLATE_HELP, apply_mapping, guess_mapping, read_table, template_bytes,
                           to_excel_bytes)
from rdai.masters import (add_alias, find_product, get_or_create_product, list_products, list_stitchers,
                          merge_products, rename_product, size_sort_key)
from rdai.units import split_pcs, units_to_pcs

st.set_page_config(page_title="RDAI Stock", page_icon="📦", layout="wide")


@st.cache_resource
def get_conn():
    return connect()


conn = get_conn()
today = dt.date.today()

PAGES = ["Ask RDAI", "Closing Stock", "Challans (Inward/Outward)", "Pending with Stitchers", "Exceptions",
         "Sales", "Opening Stock", "Physical Count & Tally", "Adjustments", "Masters", "Settings"]
page = st.sidebar.radio("Menu", PAGES)
st.sidebar.caption("All stock is calculated in **pieces**. 1 unit = 10 pcs.")


def product_names():
    return [r["name"] for r in list_products(conn)]


def stitcher_names():
    return [r["name"] for r in list_stitchers(conn)]


def download(label, sheets, file_name):
    st.download_button(label, to_excel_bytes(sheets), file_name=file_name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ------------------------------------------------------------------ upload helper

def upload_flow(kind, key, needs_date=True):
    """Upload -> map columns -> validate -> map unknown products. Returns (rows, file_name, hash) or None."""
    tmpl = {"OPENING": "opening_stock", "SALE": "sales", "COUNT": "physical_count"}[kind]
    st.download_button("Download blank template", template_bytes(tmpl), file_name=f"{tmpl}_template.xlsx",
                       key=f"{key}_tmpl")
    st.caption(TEMPLATE_HELP)
    f = st.file_uploader("Upload Excel or CSV", type=["xlsx", "xls", "csv"], key=f"{key}_file")
    if not f:
        return None
    data = f.getvalue()
    try:
        raw = read_table(f.name, data)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        return None
    st.write(f"{len(raw)} rows read. Check the column mapping:")
    guessed = guess_mapping(raw.columns)
    options = ["(ignore)", "date", "product", "size", "stock_type", "qty_units", "loose_pcs", "ref"]
    cols = st.columns(min(len(raw.columns), 6) or 1)
    mapping = {}
    for i, c in enumerate(raw.columns):
        g = guessed.get(c, "(ignore)")
        mapping[c] = cols[i % len(cols)].selectbox(str(c), options, index=options.index(g), key=f"{key}_map_{c}")
    mapping = {k: v for k, v in mapping.items() if v != "(ignore)"}
    df = apply_mapping(raw, mapping)
    required = {"product", "size"} | ({"date"} if needs_date and kind == "SALE" else set())
    if missing := required - set(df.columns):
        st.warning("Map these columns: " + ", ".join(sorted(missing)))
        return None
    if not {"qty_units", "loose_pcs"} & set(df.columns):
        st.warning("Map a quantity column (qty_units = boxes/units, loose_pcs = loose pieces).")
        return None
    default_date = None
    if "date" not in df.columns:
        default_date = st.date_input("Date for all rows", today, key=f"{key}_date")
    rows, errors = stock.prepare_rows(df, kind, default_date=default_date)
    if errors:
        st.error(f"{len(errors)} rows have problems - fix the file and upload again:")
        st.code("\n".join(errors[:50]))
        return None
    unknown = stock.unknown_products(conn, rows)
    if unknown:
        st.warning(f"{len(unknown)} product names are not in the master. Map each to an existing product "
                   "(the spelling is remembered for next time) or create it as a new product.")
        existing = product_names()
        choices = {}
        for u in unknown:
            choices[u] = st.selectbox(f"'{u}' is", ["-- choose --", "+ Create as new product"] + existing,
                                      key=f"{key}_unk_{u}")
        if st.button("Save product mapping", key=f"{key}_savemap"):
            if any(v == "-- choose --" for v in choices.values()):
                st.error("Choose an option for every name.")
            else:
                with conn:
                    for u, v in choices.items():
                        if v == "+ Create as new product":
                            get_or_create_product(conn, u)
                        else:
                            add_alias(conn, u, find_product(conn, v))
                st.rerun()
        return None
    prev = pd.DataFrame(rows)
    prev["product"] = prev["product"].map(lambda n: conn.execute(
        "SELECT name FROM products WHERE id = ?", (find_product(conn, n),)).fetchone()["name"])
    by_type = prev.groupby("stock_type")["qty_pcs"].sum()
    parts = [f"{by_type.get('FRESH', 0) // 10:,} boxes", f"{by_type.get('LOOSE', 0):,} loose pcs"] + \
        [f"{t.lower()} {q:,} pcs" for t, q in by_type.items() if t not in ("FRESH", "LOOSE")]
    st.success(f"{len(rows)} rows ready - " + ", ".join(parts) + f" (total {prev['qty_pcs'].sum():,} pcs)")
    st.dataframe(prev, width="stretch", height=250)
    return rows, f.name, stock.file_hash(data)


def batches_section(kind):
    b = stock.list_batches(conn, kind)
    if b.empty:
        return
    with st.expander(f"Previous uploads ({len(b)}) - undo a wrong upload"):
        st.dataframe(b, width="stretch", hide_index=True)
        bid = st.selectbox("Upload to delete", b["id"], key=f"del_{kind}",
                           format_func=lambda i: f"#{i} {b.set_index('id').loc[i, 'file_name']}")
        if st.button("Delete this upload", key=f"delbtn_{kind}"):
            stock.delete_batch(conn, int(bid))
            st.success("Deleted.")
            st.rerun()


# ------------------------------------------------------------------ pages

if page == "Ask RDAI":
    st.title("Ask RDAI")
    api_key = get_setting(conn, "anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.info("To use the assistant, add your Claude API key once on the **Settings** page.")
        st.stop()
    model = get_setting(conn, "model", agent.DEFAULT_MODEL)
    ss = st.session_state
    ss.setdefault("chat_history", [])     # what is sent to Claude (append-only)
    ss.setdefault("chat_display", [])     # what is shown on screen
    ss.setdefault("proposals", [])

    if not ss.chat_display:
        st.caption("Try: *What is my closing stock of Colin Classic Trunk I/E today?* · *What does each stitcher "
                   "owe me?* · *Which challans are still open?* · *I received challan 91 today, help me enter it.*")
    for role, text in ss.chat_display:
        with st.chat_message(role):
            st.markdown(text)

    for p in ss.proposals:
        if p["status"] != "pending":
            continue
        with st.container(border=True):
            st.markdown(f"**Confirm #{p['id']}:** {p['summary']}")
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Confirm", key=f"ok_{p['id']}", type="primary"):
                try:
                    res = agent.execute_proposal(conn, p)
                    p["status"] = "confirmed and saved"
                except ValueError as e:
                    res = f"Could not save: {e}"
                    p["status"] = "confirmed but failed"
                agent.note_decision(ss.chat_history, p, res)
                ss.chat_display.append(("assistant", ("✅ " if "saved" in p["status"] else "⚠️ ") + res))
                st.rerun()
            if c2.button("Cancel", key=f"no_{p['id']}"):
                p["status"] = "cancelled by the user"
                agent.note_decision(ss.chat_history, p, "Nothing was saved.")
                ss.chat_display.append(("assistant", f"Cancelled #{p['id']} - nothing saved."))
                st.rerun()

    if prompt := st.chat_input("Ask about stock, challans, stitchers..."):
        ss.chat_display.append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"), st.spinner("Checking the records..."):
            try:
                answer = agent.run_turn(conn, agent.make_client(api_key), ss.chat_history, prompt, ss.proposals,
                                        model=model)
            except agent.anthropic.AuthenticationError:
                answer = "The API key was rejected. Check it on the Settings page."
            except agent.anthropic.RateLimitError:
                answer = "Too many requests right now - wait a minute and try again."
            except agent.anthropic.APIConnectionError:
                answer = "No internet connection to Claude. The rest of the app still works offline."
            except agent.anthropic.APIStatusError as e:
                answer = f"Claude API error ({e.status_code}). Try again in a moment."
        ss.chat_display.append(("assistant", answer or "(no answer)"))
        st.rerun()
    if ss.chat_display and st.button("Start a new conversation"):
        for k in ("chat_history", "chat_display", "proposals"):
            ss[k] = []
        st.rerun()

elif page == "Closing Stock":
    st.title("Closing Stock")
    c1, c2, c3 = st.columns([1, 1, 2])
    as_of = c1.date_input("As of date", today)
    stype = c2.selectbox("Stock type", ["FRESH_ALL", "SECOND", "UNFINISHED"], format_func=lambda s: {
        "FRESH_ALL": "Fresh (boxes + loose)", "SECOND": "Seconds (Addas)", "UNFINISHED": "Unfinished panels"}[s])
    prod_filter = c3.multiselect("Products (blank = all)", product_names())
    df = stock.closing_stock(conn, as_of.isoformat())
    if not df.empty:
        df = df[df["stock_type"].isin(["FRESH", "LOOSE"] if stype == "FRESH_ALL" else [stype])]
    if prod_filter and not df.empty:
        df = df[df["product"].isin(prod_filter)]

    def show_matrix(frame, title):
        st.subheader(title)
        letter = frame["size"].map(lambda z: not str(z).isdigit())
        for caption, part in (("Number sizes", frame[~letter]), ("Letter sizes", frame[letter])):
            if not part.empty:
                st.caption(caption)
                st.dataframe(stock.stock_matrix(part).astype("Int64").astype("string").fillna(""), width="stretch")

    if df.empty:
        st.info("No stock movements yet for this selection.")
    else:
        neg = df[df["closing_pcs"] < 0]
        if stype == "FRESH_ALL":
            summ = stock.fresh_summary(df)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Boxes", f"{summ['boxes'].sum():,}")
            m2.metric("Loose pieces", f"{summ['loose_pcs'].sum():,}")
            m3.metric("Fresh total pieces", f"{summ['fresh_total_pcs'].sum():,}")
            m4.metric("Negative balances", len(neg))
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Total pieces", f"{df['closing_pcs'].sum():,}")
            m2.metric("Total units", f"{df['closing_pcs'].sum() / 10:,.1f}")
            m3.metric("Negative balances", len(neg))
        if len(neg):
            st.warning("Negative stock means sales were booked without matching opening stock or receipts, "
                       "a product name in sales is mapped to the wrong product, or loose pieces were sold from "
                       "boxes without recording 'Open boxes' on the Adjustments page.")
        if stype == "FRESH_ALL":
            boxes = df[df["stock_type"] == "FRESH"].assign(closing_pcs=lambda d: d["closing_pcs"] // 10)
            show_matrix(boxes, "Boxes (1 box = 10 pcs)")
            loose = df[(df["stock_type"] == "LOOSE") & (df["closing_pcs"] != 0)]
            if not loose.empty:
                show_matrix(loose, "Loose pieces")
            with st.expander("Detail per product and size: boxes, loose, total"):
                st.dataframe(summ, width="stretch", hide_index=True)
            sheets = {"fresh": summ, "movements": df}
        else:
            show_matrix(df, "Product x Size (pieces)")
            sheets = {"matrix": stock.stock_matrix(df), "detail": df}
        with st.expander("Detail: opening + received - sold +/- adjusted = closing"):
            st.dataframe(df, width="stretch", hide_index=True)
        download("Download Excel", sheets, f"closing_stock_{stype.lower()}_{as_of}.xlsx")
        st.subheader("Explain a number")
        e1, e2, e3 = st.columns(3)
        p = e1.selectbox("Product", sorted(df["product"].unique()))
        sz = e2.selectbox("Size", sorted(df[df["product"] == p]["size"].unique(), key=size_sort_key))
        types = ["FRESH", "LOOSE"] if stype == "FRESH_ALL" else [stype]
        et = e3.selectbox("Part", types, format_func=lambda t: {"FRESH": "Boxes", "LOOSE": "Loose"}.get(t, t))
        st.dataframe(stock.sku_ledger(conn, p, sz, et), width="stretch", hide_index=True)
    st.caption("Production-book receipts loaded from photos are dated 13-10-2026 (the book has no per-line "
               "dates), so stock 'as of' a date before that will not include them.")

elif page == "Challans (Inward/Outward)":
    st.title("Challans")
    tab_list, tab_new, tab_recv, tab_settle, tab_other = st.tabs(
        ["All challans", "New issue challan", "Record receipt", "Settle short / unfinished", "In-house & stitcher"])

    with tab_list:
        cs = prod.challan_summary(conn)
        f1, f2 = st.columns(2)
        sf = f1.multiselect("Stitcher", sorted(cs["stitcher"].unique()) if not cs.empty else [])
        only_open = f2.checkbox("Only challans with open lines")
        if not cs.empty:
            if sf:
                cs = cs[cs["stitcher"].isin(sf)]
            if only_open:
                cs = cs[cs["open_lines"] > 0]
        st.dataframe(cs, width="stretch", hide_index=True)
        cno = st.text_input("Open challan no.")
        if cno:
            lb = prod.line_balances(conn, challan_no=cno)
            st.dataframe(lb.drop(columns=["line_id"]), width="stretch", hide_index=True)
            rec = pd.read_sql_query(
                "SELECT r.id, r.receipt_date, p.name AS product, l.size, r.received_pcs, r.addas_pcs, r.short_pcs, "
                "r.unfinished_pcs, r.note FROM receipts r JOIN challan_lines l ON l.id = r.line_id "
                "JOIN products p ON p.id = l.product_id WHERE l.challan_no = ? ORDER BY r.id", conn, params=[cno])
            if not rec.empty:
                st.write("Receipts")
                st.dataframe(rec, width="stretch", hide_index=True)
                rid = st.selectbox("Delete a wrong receipt", [None] + list(rec["id"]))
                if rid and st.button("Delete receipt"):
                    prod.delete_receipt(conn, int(rid))
                    st.rerun()

    with tab_new:
        with st.form("new_challan", clear_on_submit=True):
            a, b, c, d = st.columns(4)
            cno = a.text_input("Challan no.")
            idate = b.date_input("Issue date", today)
            kind = c.selectbox("Type", CHALLAN_KINDS, format_func=lambda k: {
                "STITCHING": "Issue to stitcher", "SALE_TO_STITCHER": "Sale to stitcher",
                "INHOUSE": "RDK in-house (not issued yet)"}[k])
            stitcher = d.selectbox("Stitcher", [""] + stitcher_names() + ["+ New..."])
            new_st = st.text_input("New stitcher name (if '+ New...')")
            st.caption("Issue quantity in UNITS (e.g. 7.2 = 72 pcs). Pick products from the master - "
                       "add a new product first under Masters.")
            lines = st.data_editor(pd.DataFrame({"product": [""] * 8, "size": [""] * 8, "issue_units": [None] * 8}),
                                   num_rows="dynamic", width="stretch",
                                   column_config={"product": st.column_config.SelectboxColumn(
                                       options=product_names()), "issue_units": st.column_config.NumberColumn(
                                       format="%.1f", step=0.1)})
            if st.form_submit_button("Save challan"):
                s_name = new_st.strip() if stitcher == "+ New..." else stitcher
                ln = [r for r in lines.to_dict("records") if r["product"] and r["size"] and r["issue_units"]]
                try:
                    if kind != "INHOUSE" and not s_name:
                        raise ValueError("Choose a stitcher")
                    prod.create_challan(conn, cno, idate.isoformat(), kind, s_name or None, ln)
                    st.success(f"Challan {cno} saved with {len(ln)} lines.")
                except ValueError as e:
                    st.error(str(e))

    def pick_line(key, only_owed=False):
        cno = st.text_input("Challan no.", key=f"{key}_cno")
        if not cno:
            return None
        lb = prod.line_balances(conn, challan_no=cno)
        if lb.empty:
            st.error("No such challan.")
            return None
        if lb.iloc[0]["kind"] != "STITCHING":
            st.error(f"Challan {cno} is {lb.iloc[0]['kind']} - receipts only apply to stitching challans.")
            return None
        if only_owed:
            lb = lb[(lb["short_pcs"] > 0) | (lb["unfinished_pcs"] > 0)]
            if lb.empty:
                st.info("Nothing outstanding as short/unfinished on this challan.")
                return None
        st.dataframe(lb[["product", "size", "issued_pcs", "received_pcs", "addas_pcs", "short_pcs",
                         "unfinished_pcs", "unaccounted_pcs", "status"]], width="stretch", hide_index=True)
        opts = {f"{r.product} | {r.size}": r for r in lb.itertuples()}
        return opts[st.selectbox("Line", list(opts), key=f"{key}_line")]

    with tab_recv:
        st.caption("Received fresh in UNITS: whole units are boxes, the decimal is loose (50.5 = 50 boxes + "
                   "5 loose). Extra loose pcs, addas, short and unfinished in PIECES. "
                   "Use a negative short for extra pieces received.")
        ln = pick_line("recv")
        if ln is not None:
            with st.container(border=True):
                a, b, b2, c, d, e = st.columns(6)
                rdate = a.date_input("Receipt date", today)
                ru = b.number_input("Fresh received (units)", min_value=0.0, step=0.1, format="%.1f")
                lp = b2.number_input("Extra loose (pcs)", min_value=0, step=1)
                ad = c.number_input("Addas (pcs)", min_value=0, step=1)
                sh = d.number_input("Short (pcs)", step=1)
                uf = e.number_input("Unfinished (pcs)", min_value=0, step=1)
                note = st.text_input("Note")
                boxed, loose = split_pcs(units_to_pcs(round(ru, 1)))
                loose += lp
                fresh = boxed + loose
                after = ln.unaccounted_pcs - fresh - ad - sh - uf
                st.write(f"Fresh: **{boxed // 10} boxes + {loose} loose** ({fresh} pcs).")
                st.write(f"After this receipt, unaccounted on this line will be **{after} pcs**.")
                if st.button("Save receipt", type="primary"):
                    try:
                        prod.add_receipt(conn, ln.line_id, rdate.isoformat(), round(ru, 1), ad, sh, uf, note,
                                         loose_pcs=lp)
                        st.success("Saved.")
                    except ValueError as e:
                        st.error(str(e))

    with tab_settle:
        st.caption("Stitcher delivers finished pieces for earlier short or unfinished. Adds to fresh stock "
                   "and reduces the dues on the line.")
        ln = pick_line("settle", only_owed=True)
        if ln is not None:
            with st.form("settle"):
                a, b, c = st.columns(3)
                sdate = a.date_input("Date", today)
                against = b.selectbox("Against", ["UNFINISHED", "SHORT"])
                pcs = c.number_input("Finished pieces received", min_value=1, step=1)
                if st.form_submit_button("Save"):
                    try:
                        prod.settle_dues(conn, ln.line_id, sdate.isoformat(), pcs, against)
                        st.success("Saved.")
                    except ValueError as e:
                        st.error(str(e))

    with tab_other:
        st.subheader("Issue an in-house challan to a stitcher")
        inh = [r["challan_no"] for r in conn.execute("SELECT challan_no FROM challans WHERE kind='INHOUSE'")]
        if inh:
            a, b, c = st.columns(3)
            ch = a.selectbox("In-house challan", inh)
            stn = b.text_input("Stitcher", key="inh_st")
            idt = c.date_input("Issue date", today, key="inh_dt")
            if st.button("Mark as issued"):
                try:
                    prod.issue_inhouse(conn, ch, stn, idt.isoformat())
                    st.success("Done.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        else:
            st.caption("No in-house challans.")
        st.subheader("Assign stitcher to challans")
        st.caption("The photographed book has no stitcher names - fill them here so pending reports are per stitcher.")
        nos = st.text_input("Challan numbers (comma separated, ranges allowed e.g. 1-7, 11)")
        stn2 = st.text_input("Stitcher name", key="assign_st")
        if st.button("Assign") and nos and stn2:
            sel = []
            for part in nos.split(","):
                part = part.strip()
                if "-" in part:
                    lo, hi = part.split("-")
                    sel += [str(i) for i in range(int(lo), int(hi) + 1)]
                elif part:
                    sel.append(part)
            prod.set_stitcher(conn, sel, stn2)
            st.success(f"Assigned {len(sel)} challans to {stn2}.")

elif page == "Pending with Stitchers":
    st.title("Pending with Stitchers")
    st.caption("Owed = issued - received - addas. It includes pieces not yet received, declared short, and "
               "unfinished panels the stitcher still has to complete. Negative = extra pieces received.")
    pw = prod.pending_with_stitchers(conn)
    if pw.empty:
        st.info("Nothing pending.")
    else:
        pw["stitcher"] = pw["stitcher"].replace("", "(not assigned)")
        summ = pw.groupby("stitcher").agg(lines=("line_id", "count"), owed_pcs=("owed_by_stitcher_pcs", "sum"),
                                          not_received_pcs=("unaccounted_pcs", "sum"), short_pcs=("short_pcs", "sum"),
                                          unfinished_pcs=("unfinished_pcs", "sum")).reset_index()
        st.dataframe(summ, width="stretch", hide_index=True)
        stf = st.multiselect("Stitcher", sorted(pw["stitcher"].unique()))
        status = st.multiselect("Status", sorted(pw["status"].unique()))
        view = pw
        if stf:
            view = view[view["stitcher"].isin(stf)]
        if status:
            view = view[view["status"].isin(status)]
        st.dataframe(view.drop(columns=["line_id", "receipt_count"]), width="stretch", hide_index=True)
        download("Download Excel", {"by_stitcher": summ, "lines": view.drop(columns=["line_id"])},
                 f"pending_with_stitchers_{today}.xlsx")
    inh = prod.line_balances(conn)
    inh = inh[inh["kind"] == "INHOUSE"] if not inh.empty else inh
    if not inh.empty:
        st.subheader("RDK in-house (cut, not yet issued)")
        st.dataframe(inh[["challan_no", "product", "size", "issued_pcs", "note"]], width="stretch",
                     hide_index=True)

elif page == "Exceptions":
    st.title("Exceptions - lines to check against the book")
    st.caption("Issued should equal received + addas + short + unfinished. These lines don't balance, or were "
               "flagged while reading the photos. Fix by recording the missing receipt, or correcting the book.")
    ex = prod.exceptions(conn)
    if ex.empty:
        st.success("No exceptions.")
    else:
        st.metric("Lines to check", len(ex))
        st.dataframe(ex[["challan_no", "product", "size", "issue", "note", "issued_pcs", "received_pcs", "addas_pcs",
                         "short_pcs", "unfinished_pcs", "unaccounted_pcs", "stitcher"]],
                     width="stretch", hide_index=True)
        download("Download Excel", {"exceptions": ex.drop(columns=["line_id"])}, f"exceptions_{today}.xlsx")
        st.subheader("Clear a review note (after checking the book)")
        opts = {f"Ch {r.challan_no} | {r.product} | {r.size}": r.line_id for r in ex.itertuples() if r.note}
        if opts:
            k = st.selectbox("Line", list(opts))
            if st.button("Mark note as checked"):
                with conn:
                    conn.execute("UPDATE challan_lines SET note = NULL WHERE id = ?", (int(opts[k]),))
                st.rerun()

elif page == "Sales":
    st.title("Sales (from ERP)")
    st.caption("Export the sales report from the ERP to Excel/CSV (date, item, size, qty in units) and upload it. "
               "Item names are matched to the product master; unknown names are asked once and remembered.")
    res = upload_flow("SALE", "sale")
    if res and st.button("Import sales", type="primary"):
        try:
            bid = stock.import_entries(conn, "SALE", *res)
            st.success(f"Imported as upload #{bid}.")
        except ValueError as e:
            st.error(str(e))
    batches_section("SALE")

elif page == "Opening Stock":
    st.title("Opening Stock")
    st.warning("The production book starts on 01-04-2026, so opening stock must be the stock as on **01-04-2026** "
               "(before any of these challans were received).")
    res = upload_flow("OPENING", "open", needs_date=False)
    if res and st.button("Import opening stock", type="primary"):
        try:
            bid = stock.import_entries(conn, "OPENING", *res)
            st.success(f"Imported as upload #{bid}.")
        except ValueError as e:
            st.error(str(e))
    batches_section("OPENING")

elif page == "Physical Count & Tally":
    st.title("Physical Count & Tally")
    tab_up, tab_cmp = st.tabs(["Upload a count", "Compare with system"])
    with tab_up:
        st.caption("Count the godown, fill the template, upload. Count only what you can - uncounted items are "
                   "not touched.")
        a, b = st.columns(2)
        cdate = a.date_input("Count date", today)
        label = b.text_input("Label (e.g. 'Godown 1 - full count')")
        res = upload_flow("COUNT", "count", needs_date=False)
        if res and st.button("Save count", type="primary"):
            try:
                cid = physical.save_count(conn, cdate.isoformat(), res[0], label, res[1], res[2])
                st.success(f"Saved as count #{cid}. Open 'Compare with system'.")
            except ValueError as e:
                st.error(str(e))
    with tab_cmp:
        counts = physical.list_counts(conn)
        if counts.empty:
            st.info("No counts uploaded yet.")
        else:
            st.dataframe(counts, width="stretch", hide_index=True)
            cid = st.selectbox("Count", counts["id"], format_func=lambda i: "#{} {} {}".format(
                i, *counts.set_index("id").loc[i, ["count_date", "label"]]))
            inc = st.checkbox("Also show items with system stock that were not counted")
            rec = physical.reconcile(conn, int(cid), include_uncounted=inc)
            m1, m2, m3, m4 = st.columns(4)
            counted = rec[rec["counted"]]
            m1.metric("Lines counted", len(counted))
            m2.metric("Matching", int((counted["variance_pcs"] == 0).sum()))
            m3.metric("Shortage pcs", int(-counted.loc[counted["variance_pcs"] < 0, "variance_pcs"].sum()))
            m4.metric("Excess pcs", int(counted.loc[counted["variance_pcs"] > 0, "variance_pcs"].sum()))
            only_diff = st.checkbox("Only differences", value=True)
            view = rec[rec["variance_pcs"] != 0] if only_diff else rec
            st.dataframe(view, width="stretch", hide_index=True)
            download("Download Excel", {"tally": rec}, f"stock_tally_{rec.attrs['count_date']}.xlsx")
            if rec.attrs["adjusted"]:
                st.info("Adjustments for this count were already posted.")
            else:
                st.write("When the differences are verified, make the system match the physical count. "
                         "Each difference is saved as an adjustment with this count as reference.")
                reason = st.text_input("Reason", "Physical count")
                if st.button("Post adjustments", type="primary"):
                    n = physical.post_adjustments(conn, int(cid), reason)
                    st.success(f"Posted {n} adjustments.")
                    st.rerun()

elif page == "Adjustments":
    st.title("Adjustments")
    st.subheader("Repack fresh stock")
    st.caption("Pack loose pieces into full boxes, or open boxes into loose pieces. Total fresh stock does not change.")
    with st.form("repack", clear_on_submit=True):
        a, b, c, d, e = st.columns(5)
        rpdate = a.date_input("Date", today, key="rp_date")
        rpp = b.selectbox("Product", product_names(), key="rp_p")
        rps = c.text_input("Size", key="rp_s")
        rpd = d.selectbox("Action", ["PACK", "OPEN"], format_func=lambda x: {
            "PACK": "Pack loose into boxes", "OPEN": "Open boxes into loose"}[x])
        rpb = e.number_input("Boxes", min_value=1, step=1)
        if st.form_submit_button("Save repack"):
            try:
                stock.repack(conn, rpdate.isoformat(), rpp, rps, rpb, rpd)
                st.success("Saved.")
            except ValueError as ex:
                st.error(str(ex))
    st.subheader("Manual adjustment")
    st.caption("For corrections not covered elsewhere (damage, sample given, returns). Every adjustment needs a reason.")
    with st.form("adj", clear_on_submit=True):
        a, b, c = st.columns(3)
        adate = a.date_input("Date", today)
        p = b.selectbox("Product", product_names())
        sz = c.text_input("Size")
        d, e, f = st.columns(3)
        stype = d.selectbox("Stock type", STOCK_TYPES)
        qty = e.number_input("Pieces (+ add / - remove)", step=1)
        reason = f.text_input("Reason")
        if st.form_submit_button("Save"):
            try:
                stock.add_adjustment(conn, adate.isoformat(), p, sz, int(qty), stype, reason)
                st.success("Saved.")
            except ValueError as e:
                st.error(str(e))
    adj = pd.read_sql_query(
        "SELECT e.id, e.entry_date, p.name AS product, e.size, e.stock_type, e.qty_pcs, e.ref, e.note "
        "FROM stock_entries e JOIN products p ON p.id = e.product_id WHERE e.kind = 'ADJUSTMENT' ORDER BY e.id DESC",
        conn)
    st.dataframe(adj, width="stretch", hide_index=True)

elif page == "Masters":
    st.title("Masters")
    t1, t2 = st.tabs(["Products", "Stitchers"])
    with t1:
        prods = pd.read_sql_query(
            "SELECT p.id, p.name, GROUP_CONCAT(a.alias_key, ' | ') AS known_spellings FROM products p "
            "LEFT JOIN product_aliases a ON a.product_id = p.id GROUP BY p.id ORDER BY p.name", conn)
        st.dataframe(prods, width="stretch", hide_index=True)
        names = dict(zip(prods["name"], prods["id"]))
        st.subheader("Add product")
        np_name = st.text_input("Product name", key="np_name")
        if st.button("Add product") and np_name.strip():
            if find_product(conn, np_name):
                st.error("A product with this name/spelling already exists.")
            else:
                with conn:
                    get_or_create_product(conn, np_name)
                st.rerun()
        st.subheader("Rename")
        a, b = st.columns(2)
        old = a.selectbox("Product", list(names), key="ren_old")
        new = b.text_input("New name")
        if st.button("Rename") and new:
            with conn:
                rename_product(conn, names[old], new)
            st.rerun()
        st.subheader("Merge duplicates")
        st.caption("If two names are the same product, merge them. All challans, sales and counts move to the kept one.")
        a, b = st.columns(2)
        keep = a.selectbox("Keep", list(names), key="m_keep")
        drop = b.selectbox("Merge into it and remove", list(names), key="m_drop")
        if st.button("Merge") and keep != drop:
            try:
                merge_products(conn, names[keep], names[drop])
                st.rerun()
            except Exception as e:
                st.error(f"Could not merge (same challan has both products in the same size?): {e}")
        st.subheader("Add a spelling (e.g. ERP item name)")
        a, b = st.columns(2)
        al = a.text_input("Spelling")
        tgt = b.selectbox("Means product", list(names), key="al_tgt")
        if st.button("Add spelling") and al:
            with conn:
                add_alias(conn, al, names[tgt])
            st.success("Saved.")
    with t2:
        st.dataframe(pd.read_sql_query(
            "SELECT s.name, COUNT(c.challan_no) AS challans FROM stitchers s LEFT JOIN challans c "
            "ON c.stitcher_id = s.id GROUP BY s.id ORDER BY s.name", conn), width="stretch", hide_index=True)

elif page == "Settings":
    st.title("Settings")
    st.subheader("Claude API key (for Ask RDAI)")
    st.caption("Create a key at console.anthropic.com -> API Keys, add some credit, and paste it here. "
               "It is stored only in this computer's database file. Each question costs a few rupees.")
    has = bool(get_setting(conn, "anthropic_api_key"))
    st.write("Status: " + ("key saved ✅" if has else "no key saved"))
    k = st.text_input("Paste API key", type="password")
    if st.button("Save key") and k.strip():
        set_setting(conn, "anthropic_api_key", k.strip())
        st.success("Saved.")
        st.rerun()
    if has and st.button("Remove key"):
        set_setting(conn, "anthropic_api_key", None)
        st.rerun()
    st.subheader("Model")
    cur = get_setting(conn, "model", agent.DEFAULT_MODEL)
    m = st.selectbox("Claude model", list(agent.MODELS), index=list(agent.MODELS).index(cur),
                     format_func=agent.MODELS.get)
    if m != cur:
        set_setting(conn, "model", m)
        st.success("Saved.")
    st.subheader("Data file")
    st.code(os.path.abspath(conn.execute("PRAGMA database_list").fetchone()["file"]))
    st.caption("Back up this one file daily (pen drive / Google Drive). It holds all your data.")
