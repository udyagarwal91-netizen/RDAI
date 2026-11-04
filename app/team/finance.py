"""💰 Finance: keeps score in money. Every morning it reads Meta ads results (Skipper Packs ad account only) and
Shopify sales, updates the goal numbers, checks budget pacing, and alerts you at once if something's off.
Every week it writes a money brief for the Manager.

All maths is done in code from real numbers; Claude only explains them. Missing business numbers
(product cost etc.) are reported as missing — never assumed.
"""

import json
import logging
from datetime import date, datetime, timedelta

from app import claude, goals, meta_ads, sales
from app.brands import Brand
from app.db import connect, get_state, set_state
from app.team import AGENTS, post_brief, say, skill

log = logging.getLogger(__name__)


def _today() -> date:
    return datetime.now(meta_ads.IST).date()  # the business runs on India time


def ai_cost_this_month(brand: Brand) -> float:
    """What the agent's AI work cost this month (US$): drafts + team briefs."""
    start = _today().replace(day=1).isoformat()
    with connect() as conn:
        drafts = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM drafts WHERE brand = ? AND created_at >= ?",
                              (brand.slug, start)).fetchone()[0]
        briefs = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM team_briefs WHERE brand = ? AND created_at >= ?",
                              (brand.slug, start)).fetchone()[0]
    return round(drafts + briefs, 2)


def break_even_roas(brand: Brand, aov: float | None) -> tuple[float | None, list[str]]:
    """The ROAS where ads stop losing money. Returns (value or None, list of missing inputs)."""
    biz = goals.load(brand)["business"]
    missing = [k for k in ("product_cost_pct", "shipping_cost_per_order", "payment_fee_pct", "cod_share_pct", "rto_pct")
               if biz.get(k) is None]
    if biz.get("product_cost_pct") is None or not aov:
        return None, missing
    cost = biz["product_cost_pct"] / 100
    fee = (biz.get("payment_fee_pct") or 0) / 100
    ship = (biz.get("shipping_cost_per_order") or 0) / aov
    lost = (biz.get("cod_share_pct") or 0) / 100 * (biz.get("rto_pct") or 0) / 100
    margin = 1 - cost - fee - ship - lost * (1 + ship)  # refused COD orders: no sale, shipping paid twice
    return (round(1 / margin, 2) if margin > 0 else None), missing


def measure(brand: Brand, refresh: bool = True) -> dict:
    """Fresh numbers for the last 7 full days. Problems (permissions etc.) are listed, not hidden."""
    problems = []
    if refresh:
        if meta_ads.account_id(brand) or brand.secret("META_ACCESS_TOKEN"):
            try:
                if not meta_ads.account_id(brand):
                    meta_ads.discover(brand)
                meta_ads.refresh(brand)
            except Exception as e:  # noqa: BLE001 — explained to the owner, never crashes the morning check
                problems.append(f"Meta ads: {e}")
        try:
            sales.refresh(brand)
        except Exception as e:  # noqa: BLE001
            problems.append(f"Shopify sales: {e}")
        if sales.funnel(brand) is None:
            problems.append("Checkout numbers: Shopify didn't share them (the app needs the read_reports permission).")
    ads7, ads_prev = meta_ads.totals(brand, 7), meta_ads.totals(brand, 7, end=_today() - timedelta(days=8))
    s7 = sales.totals(brand, 7)
    f = sales.last_funnel(brand)
    with connect() as conn:
        active_ads = conn.execute("SELECT COUNT(DISTINCT ad_id) FROM ads_daily WHERE brand = ? AND day >= ? AND "
                                  "spend > 0", (brand.slug, (_today() - timedelta(days=7)).isoformat())).fetchone()[0]
    blended = round(s7["revenue"] / ads7["spend"], 2) if ads7["spend"] and s7["orders"] else None
    m = {"blended_roas": blended, "meta_roas": ads7["roas"], "aov": s7["aov"], "cpa": ads7["cpa"],
         "link_ctr": ads7["link_ctr"], "refund_rate": s7["refund_rate"], "repeat_rate": s7["repeat_rate"],
         "checkout_completion": (f or {}).get("checkout_completion"),
         "active_ads": active_ads if ads7["spend"] else None}
    goals.record(brand, m, "Finance agent")
    be, missing = break_even_roas(brand, s7["aov"])
    out = {"metrics": m, "ads": ads7, "ads_prev": ads_prev, "sales": s7, "funnel": f, "break_even_roas": be,
           "missing_business_numbers": missing, "month_spend": meta_ads.month_spend(brand),
           "ai_cost_usd_month": ai_cost_this_month(brand), "problems": problems,
           "measured_at": datetime.now().strftime("%d %b %I:%M %p")}
    set_state(brand.slug, "finance:latest", json.dumps(out, default=str))
    return out


def latest_numbers(brand: Brand) -> dict | None:
    raw = get_state(brand.slug, "finance:latest")
    return json.loads(raw) if raw else None


def _alert_once(brand: Brand, kind: str, text: str) -> bool:
    key = f"finance:alert:{_today().isoformat()}:{kind}"
    if get_state(brand.slug, key):
        return False
    set_state(brand.slug, key, "1")
    say(brand, "finance", "⚠️ " + text)
    return True


def alerts(brand: Brand, m: dict) -> list[str]:
    """Problems worth telling you about right away (at most once a day each)."""
    g = goals.load(brand)["guardrails"]
    sent = []
    yesterday = meta_ads.daily(brand, 2)[0]
    if yesterday["spend"] > float(g["daily_ad_budget"]) * 1.1:
        text = (f"Yesterday's ad spend was ₹{yesterday['spend']:g}, above your daily limit of "
                f"₹{g['daily_ad_budget']:g}. Check budgets in Ads Manager or the Ads page.")
        sent += [text] if _alert_once(brand, "overspend", text) else []
    target_cpa = next((p["target_now"] for p in goals.progress(brand) if p["key"] == "cpa"), None) or 100
    if yesterday["spend"] >= 3 * target_cpa and not yesterday["purchases"]:
        text = (f"₹{yesterday['spend']:g} spent on ads yesterday with no recorded sale. Could be a slow day — "
                "or tracking may be broken. The SMO will check the ads.")
        sent += [text] if _alert_once(brand, "nosales", text) else []
    a, s = m["ads"], m["sales"]
    if a["purchases"] >= 10 and s["orders"] >= 10:
        gap = abs(a["purchases"] - s["orders"]) / max(a["purchases"], s["orders"])
        if gap > 0.4:
            text = (f"Meta counts {a['purchases']} sales from ads but Shopify shows {s['orders']} orders in total "
                    "(last 7 days). Tracking may be broken, so budget raises are frozen until they match again.")
            set_state(brand.slug, "finance:tracking_suspect", "1")
            sent += [text] if _alert_once(brand, "tracking", text) else []
        else:
            set_state(brand.slug, "finance:tracking_suspect", "")
    today = _today()
    days_in_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    pace = m["month_spend"] / max(1, today.day) * days_in_month.day
    if pace > float(g["monthly_ad_budget"]) * 1.05:
        text = (f"At this pace ad spend this month ends near ₹{pace:,.0f}, above your monthly limit of "
                f"₹{g['monthly_ad_budget']:,.0f}.")
        sent += [text] if _alert_once(brand, "pace", text) else []
    ai_inr = m["ai_cost_usd_month"] * 88
    if ai_inr > float(g["ai_cost_monthly"]) * 0.8:
        text = f"AI costs this month are about ₹{ai_inr:,.0f} (limit ₹{g['ai_cost_monthly']:,.0f})."
        sent += [text] if _alert_once(brand, "aicost", text) else []
    return sent


def daily_tick(brand: Brand) -> dict:
    m = measure(brand)
    m["alerts"] = alerts(brand, m) if m["ads"]["spend"] or m["sales"]["orders"] else []
    return m


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"},
                   "what_the_numbers_say": {"type": "array", "items": {"type": "string"}},
                   "biggest_lever": {"type": "string", "description": "What holds ROAS back most, with numbers."},
                   "actions": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
                   "budget_note": {"type": "string"}},
    "required": ["headline", "what_the_numbers_say", "biggest_lever", "actions", "budget_note"],
    "additionalProperties": False,
}


def weekly_brief(brand: Brand, client=None) -> dict | None:
    m = measure(brand)
    if not (m["ads"]["spend"] or m["sales"]["orders"]):
        return None  # nothing to say without numbers
    client = client or claude.client_for(brand)
    numbers = {k: m[k] for k in ("metrics", "ads", "ads_prev", "sales", "funnel", "break_even_roas",
                                 "missing_business_numbers", "month_spend", "ai_cost_usd_month", "problems")}
    system = f"You are the {AGENTS['finance']['name']} agent of {brand.name}'s team.\n\n{skill(brand, 'finance')}"
    user = (f"Write this week's money brief for the Manager. Use only these numbers (₹; 'ads_prev' is the week "
            f"before):\n{numbers}\n\nGOALS AND LIMITS:\n{goals.summary_for_agents(brand)}")
    data, cost, _ = claude.ask(client, system, [{"role": "user", "content": user}], BRIEF_SCHEMA, effort="medium")
    body = "\n".join([data["headline"], "", *[f"- {x}" for x in data["what_the_numbers_say"]], "",
                      f"Biggest lever: {data['biggest_lever']}", "", "Actions:",
                      *[f"- {x}" for x in data["actions"]], "", f"Budget: {data['budget_note']}",
                      *(["", "Couldn't measure: " + "; ".join(m["problems"])] if m["problems"] else [])])
    brief_id = post_brief(brand, "finance", "finance", data["headline"][:200], body, {**data, "numbers": numbers},
                          cost)
    return {"id": brief_id, "data": data, "cost_usd": cost, "body": body}
