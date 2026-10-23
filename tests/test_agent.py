"""Agent loop tests with a scripted fake Claude client (no network, no API key)."""
import json
from types import SimpleNamespace as NS

import pytest

from rdai import agent, stock
from rdai.db import connect
from rdai.seed import load_ledger_csv


def text(t):
    return NS(type="text", text=t)


def tool(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    """Returns scripted responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        content, stop = self.responses.pop(0)
        return NS(content=content, stop_reason=stop)


@pytest.fixture
def conn():
    c = connect(":memory:")
    load_ledger_csv(c)
    return c


def test_read_tool_round_trip(conn):
    fc = FakeClient([
        ([tool("t1", "get_closing_stock", {"as_of_date": "2026-10-22", "product": "Colin Classic Pkt Boxer"})], "tool_use"),
        ([text("You have 3,941 pcs.")], "end_turn"),
    ])
    history, proposals = [], []
    out = agent.run_turn(conn, fc, history, "stock of pkt boxer?", proposals, today="2026-10-22")
    assert out == "You have 3,941 pcs."
    # the engine's number reached Claude in the tool result
    tool_result = fc.requests[1]["messages"][-1]["content"][0]
    payload = json.loads(tool_result["content"])
    totals = payload["totals_by_stock_type_pcs"]
    assert totals["FRESH"] + totals["LOOSE"] == 3941  # 739 + 1643 + 1559 pcs received
    assert {"boxes", "loose_pcs", "fresh_total_pcs"} <= set(payload["fresh_summary"][0])
    assert fc.requests[0]["fallbacks"] == "default" and fc.requests[0]["model"] == "claude-opus-5-5"
    # history is append-only: user, assistant(tool_use), user(tool_result), assistant(text)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_unknown_product_suggests_names(conn):
    res = agent.run_read_tool(conn, "get_closing_stock", {"as_of_date": "2026-10-22", "product": "colin pkt boxr"})
    assert "Colin Classic Pkt Boxer" in res["did_you_mean"]


def test_every_read_tool_runs_on_real_data(conn):
    for name, args in [("find_products", {"query": "inlike"}), ("explain_stock", {"product": "Colin Inlike Wt RN", "size": "90"}),
                       ("list_challans", {"only_open": True}), ("get_challan", {"challan_no": "55"}),
                       ("pending_with_stitchers", {}), ("get_exceptions", {}), ("get_physical_tally", {})]:
        res = agent.run_read_tool(conn, name, args)
        assert isinstance(res, dict), name


def test_write_tools_only_propose_until_confirmed(conn):
    fc = FakeClient([
        ([tool("t1", "propose_receipt", {"challan_no": "91", "product": "Just Joey Ladla Drawer", "size": "60",
                                          "receipt_date": "2026-10-22", "received_units": 55.5, "addas_pcs": 5})],
         "tool_use"),
        ([text("Please confirm.")], "end_turn"),
    ])
    history, proposals = [], []
    agent.run_turn(conn, fc, history, "received ch 91 size 60", proposals)
    assert len(proposals) == 1 and proposals[0]["status"] == "pending"
    assert stock.closing_stock(conn, "2026-10-22", product="Just Joey Ladla Drawer").empty  # nothing saved yet

    agent.execute_proposal(conn, proposals[0])
    cs = stock.closing_stock(conn, "2026-10-22", product="Just Joey Ladla Drawer").set_index("stock_type")
    # 55.5 units = 55 boxes + 5 loose
    assert cs.loc["FRESH", "closing_pcs"] == 550 and cs.loc["LOOSE", "closing_pcs"] == 5
    assert cs.loc["SECOND", "closing_pcs"] == 5


def test_invalid_proposal_returns_error_to_claude(conn):
    fc = FakeClient([
        ([tool("t1", "propose_issue_challan", {"challan_no": "55", "issue_date": "2026-10-22", "kind": "STITCHING",
                                                "stitcher": "X", "lines": []})], "tool_use"),
        ([text("Challan 55 exists.")], "end_turn"),
    ])
    proposals = []
    agent.run_turn(conn, fc, [], "make challan 55", proposals)
    err = fc.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "already exists" in err["content"] and not proposals
