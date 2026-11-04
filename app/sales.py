"""Shopify sales numbers for the Finance agent: orders and revenue per day, refunds, cash-on-delivery share,
repeat buyers, and (if Shopify allows) the checkout funnel.

Needs extra read permissions on the Shopify app: read_orders (sales) and, for the checkout funnel,
read_reports. Without them the agent says exactly what's missing instead of guessing numbers.
"""

import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import shopify
from app.brands import Brand
from app.db import connect, get_state, set_state

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

ORDERS = """
query Orders($q: String!, $after: String) {
  orders(first: 100, after: $after, query: $q, sortKey: CREATED_AT) {
    nodes { createdAt cancelledAt test paymentGatewayNames
            totalPriceSet { shopMoney { amount } } totalRefundedSet { shopMoney { amount } } %CUSTOMER% }
    pageInfo { hasNextPage endCursor }
  }
}"""

PERMISSION_HELP = ("Shopify needs one more permission for sales numbers. In the Shopify Dev Dashboard → your app → "
                   "Versions → Create version: tick read_orders (and read_reports for checkout numbers), release it, "
                   "then open the install link again and approve.")


class SalesError(Exception):
    pass


def _orders(brand: Brand, since: date) -> list[dict]:
    """All orders since a date. Asks for each buyer's order count when Shopify allows it."""
    q = f"created_at:>={since.isoformat()}"
    for customer_part in ("customer { numberOfOrders }", ""):
        query = ORDERS.replace("%CUSTOMER%", customer_part)
        try:
            nodes, after = [], None
            for _ in range(50):
                data = shopify.graphql(brand, query, {"q": q, "after": after})["orders"]
                nodes += data["nodes"]
                if not data["pageInfo"]["hasNextPage"]:
                    return nodes
                after = data["pageInfo"]["endCursor"]
            return nodes
        except shopify.ShopifyError as e:
            text = str(e).lower()
            if customer_part and ("customer" in text or "permission" in text or "access" in text):
                log.info("Shopify won't share customer order counts; repeat buyers can't be measured")
                continue
            if "permission" in text or "access" in text:
                raise SalesError(PERMISSION_HELP) from None
            raise SalesError(str(e)) from None
    return []


def _is_cod(gateways: list[str]) -> bool:
    names = " ".join(gateways or []).lower()
    return "cash on delivery" in names or "(cod)" in names or names.strip() == "cod"


def refresh(brand: Brand, days: int = 35) -> int:
    """Re-read the last `days` days of orders into sales_daily. Returns the number of orders read."""
    since = datetime.now(IST).date() - timedelta(days=days)
    orders = [o for o in _orders(brand, since) if not o.get("test")]
    per_day: dict[str, dict] = {}
    for o in orders:
        day = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(IST).date().isoformat()
        d = per_day.setdefault(day, {"orders": 0, "revenue": 0.0, "refunds": 0.0, "cod_orders": 0,
                                     "repeat_orders": 0, "cancelled": 0, "repeat_known": False})
        if o.get("cancelledAt"):
            d["cancelled"] += 1
            continue
        d["orders"] += 1
        d["revenue"] += float(o["totalPriceSet"]["shopMoney"]["amount"])
        d["refunds"] += float((o.get("totalRefundedSet") or {}).get("shopMoney", {}).get("amount") or 0)
        d["cod_orders"] += _is_cod(o.get("paymentGatewayNames"))
        cust = o.get("customer")
        if cust is not None or "customer" in o:
            d["repeat_known"] = True
            d["repeat_orders"] += int((cust or {}).get("numberOfOrders") or 0) > 1
    with connect() as conn:
        conn.execute("DELETE FROM sales_daily WHERE brand = ? AND day >= ?", (brand.slug, since.isoformat()))
        for day, d in per_day.items():
            conn.execute("INSERT INTO sales_daily (brand, day, orders, revenue, refunds, cod_orders, repeat_orders, "
                         "cancelled) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (brand.slug, day, d["orders"], round(d["revenue"], 2), round(d["refunds"], 2),
                          d["cod_orders"], d["repeat_orders"] if d["repeat_known"] else None, d["cancelled"]))
    set_state(brand.slug, "sales:last_refresh", datetime.now(IST).isoformat(timespec="seconds"))
    log.info("Shopify sales refreshed: %d orders since %s", len(orders), since)
    return len(orders)


def totals(brand: Brand, days: int = 7, end: date | None = None) -> dict:
    """Sums over the last `days` full days up to and including `end` (default: yesterday)."""
    end = end or (datetime.now(IST).date() - timedelta(days=1))
    start = end - timedelta(days=days - 1)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM sales_daily WHERE brand = ? AND day BETWEEN ? AND ?",
                                              (brand.slug, start.isoformat(), end.isoformat()))]
    orders = sum(r["orders"] or 0 for r in rows)
    revenue = sum(r["revenue"] or 0 for r in rows)
    refunds = sum(r["refunds"] or 0 for r in rows)
    repeat_known = [r for r in rows if r["repeat_orders"] is not None]
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "orders": orders,
            "revenue": round(revenue, 2), "refunds": round(refunds, 2),
            "aov": round(revenue / orders, 2) if orders else None,
            "refund_rate": round(100 * refunds / revenue, 2) if revenue else None,
            "cod_share": round(100 * sum(r["cod_orders"] or 0 for r in rows) / orders, 1) if orders else None,
            "repeat_rate": round(100 * sum(r["repeat_orders"] for r in repeat_known) /
                                 max(1, sum(r["orders"] or 0 for r in repeat_known)), 1) if repeat_known else None}


def daily(brand: Brand, days: int = 14) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM sales_daily WHERE brand = ? ORDER BY day DESC LIMIT ?",
                                              (brand.slug, days))][::-1]


def funnel(brand: Brand, days: int = 14) -> dict | None:
    """Store visits → cart → checkout → paid, from Shopify's own reports (needs read_reports). None if not allowed."""
    query = ("FROM sessions SHOW sessions, sessions_with_cart_additions, sessions_that_reached_checkout, "
             f"sessions_that_completed_checkout SINCE -{days}d UNTIL yesterday")
    try:
        data = shopify.graphql(brand, "query Q($q: String!) { shopifyqlQuery(query: $q) { parseErrors "
                                      "tableData { columns { name } rows } } }", {"q": query}).get("shopifyqlQuery")
        if not isinstance(data, dict):
            raise shopify.ShopifyError("no report data returned")
    except shopify.ShopifyError as e:
        log.info("Checkout funnel not available from Shopify: %s", e)
        set_state(brand.slug, "sales:funnel_problem", "Shopify didn't share checkout numbers (needs read_reports).")
        return None
    table = data.get("tableData") or {}
    rows = table.get("rows") or []
    if data.get("parseErrors") or not rows:
        return None
    names = [c["name"] for c in table["columns"]]
    row = rows[0] if isinstance(rows[0], dict) else dict(zip(names, rows[0]))
    num = {k: float(row.get(k) or 0) for k in names}
    reached = num.get("sessions_that_reached_checkout") or 0
    out = {"sessions": int(num.get("sessions", 0)), "cart": int(num.get("sessions_with_cart_additions", 0)),
           "checkout": int(reached), "paid": int(num.get("sessions_that_completed_checkout", 0)),
           "checkout_completion": round(100 * num.get("sessions_that_completed_checkout", 0) / reached, 1)
           if reached else None}
    set_state(brand.slug, "sales:funnel", json.dumps(out))
    set_state(brand.slug, "sales:funnel_problem", "")
    return out


def last_funnel(brand: Brand) -> dict | None:
    return json.loads(get_state(brand.slug, "sales:funnel") or "null")
