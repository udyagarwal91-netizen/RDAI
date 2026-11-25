"""Book-page sync: only what is new is added, nothing is double-counted."""
import io
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from rdai import agent, book_sync, production as prod, stock
from rdai.db import connect
from rdai.seed import load_ledger_csv


@pytest.fixture
def conn():
    c = connect(":memory:")
    load_ledger_csv(c)
    return c


def L(cno, product, size, issue, rec=None, addas=None, short=None, unf=None):
    d = {"challan_no": cno, "product": product, "size": size, "issue_units": issue}
    for k, v in (("receive_units", rec), ("addas_pcs", addas), ("short_pcs", short), ("unfinished_pcs", unf)):
        if v is not None:
            d[k] = v
    return d


def fresh(conn, product, size):
    cs = stock.closing_stock(conn, "2026-12-31", product=product)
    cs = cs[(cs["size"] == size) & cs["stock_type"].isin(["FRESH", "LOOSE"])]
    return int(cs["closing_pcs"].sum())


def test_receipt_for_the_difference_only(conn):
    # Challan 96 size 110: app has 16.0 received of 30.0. Book now shows 29.5 received + 5 addas.
    before = fresh(conn, "Colin Classic Trunk I/E", "110")
    page = [L("96", "colin classic trunk 1/e", "110", 30.0, 29.5, 5)]
    plan = book_sync.plan_book_update(conn, page, "2026-11-25")
    [a] = plan["actions"]
    assert (a["type"], a["received"], a["addas"]) == ("receipt", 135, 5)
    book_sync.apply_book_update(conn, page, "2026-11-25")
    assert fresh(conn, "Colin Classic Trunk I/E", "110") == before + 135
    # sending the same page again changes nothing
    assert book_sync.plan_book_update(conn, page, "2026-11-25")["actions"] == []
    with pytest.raises(ValueError, match="Nothing to change"):
        book_sync.apply_book_update(conn, page, "2026-11-25")


def test_new_challan_with_receipts(conn):
    page = [L("114", "Colin Print Trunk I/E", "90", 20.0, 19.5, 5), L("114", "Colin Print Trunk I/E", "95", 10.0)]
    plan = book_sync.apply_book_update(conn, page, "2026-11-25")
    assert [a["type"] for a in plan["actions"]] == ["new_challan"]
    b = prod.line_balances(conn, challan_no="114").set_index("size")
    assert (b.loc["90", "issued_pcs"], b.loc["90", "received_pcs"], b.loc["90", "unaccounted_pcs"]) == (200, 195, 0)
    assert b.loc["95", "status"] == "Not received"
    assert any("no stitcher" in w for w in plan["warnings"])


def test_conflicts_are_reported_not_applied(conn):
    page = [
        L("92", "Colin Classic Trunk I/E", "110", 64.0, 61.9, 11),       # issue differs, receipts same
        L("93", "Skipper Gold Dyed Panty", "90", 105.0, 100.0, 8),       # book shows less than recorded
        L("93", "Skipper Gold Dyed Panty", "85", 98.0, 98.0),            # already up to date
    ]
    plan = book_sync.plan_book_update(conn, page, "2026-11-25")
    assert plan["actions"] == [] and plan["unchanged"] == 2
    text = " ".join(plan["warnings"])
    assert "issue differs" in text and "LESS than already recorded" in text


def test_unknown_product_and_duplicates_block_the_page(conn):
    plan = book_sync.plan_book_update(conn, [L("200", "Mystery Vest", "90", 1.0)], "2026-11-25")
    assert plan["errors"] and "unknown product" in plan["errors"][0]
    dup = [L("200", "Colin Rib Trunk O/E", "90", 1.0)] * 2
    assert "appears twice" in book_sync.plan_book_update(conn, dup, "2026-11-25")["errors"][0]


def test_unbalanced_book_line_is_flagged(conn):
    plan = book_sync.plan_book_update(conn, [L("201", "Colin Rib Trunk O/E", "90", 10.0, 9.0, 2)], "2026-11-25")
    assert any("does not balance" in w for w in plan["warnings"])


def jpeg(w=3000, h=4000):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="JPEG")
    return buf.getvalue()


class FakeClient:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        content, stop = self.responses.pop(0)
        return NS(content=content, stop_reason=stop)


def test_photo_to_confirmed_stock_change(conn):
    page = {"entry_date": "2026-11-25",
            "lines": [L("98", "Colin Rib Trunk O/E", "95", 12.9, 12.9), L("98", "Colin Rib Trunk O/E", "100", 14.7)]}
    fc = FakeClient([
        ([NS(type="tool_use", id="t1", name="propose_book_update", input=page)], "tool_use"),
        ([NS(type="text", text="Please confirm.")], "end_turn"),
    ])
    history, proposals = [], []
    agent.run_turn(conn, fc, history, "", proposals, images=[jpeg()])
    first = fc.requests[0]["messages"][0]["content"]
    assert first[0]["type"] == "image" and first[-1]["type"] == "text"
    img = Image.open(io.BytesIO(__import__("base64").b64decode(first[0]["source"]["data"])))
    assert max(img.size) == agent.MAX_IMAGE_SIDE          # phone photo shrunk
    [p] = proposals
    assert "Challan 98 Colin Rib Trunk O/E 95, received now" in p["summary"]
    before = fresh(conn, "Colin Rib Trunk O/E", "95")
    agent.execute_proposal(conn, p)
    assert fresh(conn, "Colin Rib Trunk O/E", "95") == before + 129


def test_bad_photo_changes_nothing(conn):
    history = []
    with pytest.raises(agent.UnreadableImage):
        agent.run_turn(conn, FakeClient([]), history, "", [], images=[b"not an image"])
    assert history == []


def bill_args(bill="INV-101", **extra):
    return {"date": "2026-11-25", "bill_no": bill, "party": "Sharma Traders",
            "lines": [{"product": "Colin Classic Pkt Boxer", "size": "85", "qty_units": 2.5},
                      {"product": "Colin Classic Pkt Boxer", "size": "90", "qty_units": 1, "stock_type": "SECOND"}],
            **extra}


def test_sales_bill_reduces_stock_once(conn):
    before = stock.closing_stock(conn, "2026-12-31", product="Colin Classic Pkt Boxer")
    before = before.groupby(["size", "stock_type"])["closing_pcs"].sum()
    summary = agent.validate_proposal(conn, "propose_sales_bill", bill_args())
    assert "INV-101" in summary and "35 pcs" in summary
    agent.execute_proposal(conn, {"tool": "propose_sales_bill", "args": bill_args(), "summary": summary})
    after = stock.closing_stock(conn, "2026-12-31", product="Colin Classic Pkt Boxer")
    after = after.groupby(["size", "stock_type"])["closing_pcs"].sum()
    assert before[("85", "FRESH")] - after[("85", "FRESH")] == 20    # 2 boxes
    assert before[("85", "LOOSE")] - after[("85", "LOOSE")] == 5     # + 5 loose
    assert before[("90", "SECOND")] - after[("90", "SECOND")] == 10
    # the same bill again - from a photo or from an ERP file - is refused
    with pytest.raises(ValueError, match="already recorded"):
        agent.validate_proposal(conn, "propose_sales_bill", bill_args())
    rows = [dict(date="2026-11-25", product="Colin Classic Pkt Boxer", size="85", qty_pcs=20, stock_type="FRESH",
                 ref="INV-101")]
    with pytest.raises(ValueError, match="already recorded"):
        stock.import_entries(conn, "SALE", rows, "erp.xlsx", "hash-x")


def test_sales_bill_needs_bill_number_and_known_products(conn):
    with pytest.raises(ValueError, match="Bill number"):
        agent.validate_proposal(conn, "propose_sales_bill", bill_args(bill=" "))
    bad = bill_args(lines=[{"product": "Mystery Vest", "size": "M", "qty_units": 1}])
    with pytest.raises(ValueError, match="Unknown product"):
        agent.validate_proposal(conn, "propose_sales_bill", bad)
