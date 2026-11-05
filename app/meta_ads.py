"""Reading Meta ads results (Marketing API v26.0) for the SMO and Finance agents.

Only ONE ad account is ever used: the one whose name matches the brand (for Skipper Packs: the
"Skipper Packs" ad account), or the one you pick on the Ads page. Every other ad account your Meta login
can see is ignored.

Reading needs the ads_read permission and the ad account assigned to your system user
(business.facebook.com → Business Settings → Accounts → Ad accounts → your account → Assign people →
your system user → "View performance"; for the team's ad changes later, "Manage campaigns").
"""

import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import publisher
from app.brands import Brand
from app.db import connect, get_state, set_state

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

INSIGHT_FIELDS = ["ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name", "spend",
                  "impressions", "reach", "frequency", "inline_link_clicks", "actions", "action_values"]
# Meta reports the same event under several names; the first one present is used.
PURCHASE = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
ADD_TO_CART = ("omni_add_to_cart", "add_to_cart", "offsite_conversion.fb_pixel_add_to_cart")
CHECKOUT = ("omni_initiated_checkout", "initiate_checkout", "offsite_conversion.fb_pixel_initiate_checkout")

ACCESS_HELP = ("Meta won't share your ads yet — the token is missing the ad permissions. Fix: (1) business.facebook.com "
               "→ Settings → Users → System users → your system user → Assign assets → Ad accounts → tick your ad account "
               "({name}) → "
               "turn on Manage campaigns → Assign. (2) On the same page: Generate new token → your app → expiry Never → "
               "tick everything as before PLUS ads_read and ads_management → copy it. (3) Here: Connect accounts → "
               "Instagram + Facebook → paste → Save & test. If ads_read isn't in the list: developers.facebook.com → "
               "your app → Use cases → add “Create & manage ads with Marketing API”, then try (2) again.")


class AdsError(Exception):
    pass


def _token(brand: Brand) -> str:
    token = brand.secret("META_ACCESS_TOKEN")
    if not token:
        raise AdsError("Instagram + Facebook isn't connected yet (Connect accounts → Instagram + Facebook).")
    return token


def account_id(brand: Brand) -> str | None:
    """The one ad account the team may use, as 'act_123…' (None until found or chosen)."""
    raw = get_state(brand.slug, "meta:ad_account") or str(brand.config["meta"].get("ad_account_id") or "")
    raw = raw.strip().removeprefix("act_")
    return f"act_{raw}" if raw.isdigit() else None


def set_account(brand: Brand, raw: str) -> str:
    raw = raw.strip().removeprefix("act_")
    if not raw.isdigit():
        raise AdsError("The ad account ID is a number like 1840946140217408 (Ads Manager shows it at the top left).")
    set_state(brand.slug, "meta:ad_account", raw)
    set_state(brand.slug, "meta:structure", "")  # forget the old account's campaigns
    log.info("Ad account set to act_%s", raw)
    try:
        read_currency(brand)
    except AdsError as e:
        log.info("Ad account currency not readable yet: %s", e)
    return f"act_{raw}"


def read_currency(brand: Brand) -> str:
    """Ask Meta which currency the ad account uses (₹ accounts need no conversion)."""
    body = _call(brand, "", fields="name,currency")
    set_state(brand.slug, "meta:currency", body.get("currency") or "INR")
    set_state(brand.slug, "meta:account_name", body.get("name") or "")
    return body.get("currency") or "INR"


def currency(brand: Brand) -> str:
    return get_state(brand.slug, "meta:currency") or "INR"


def to_inr(brand: Brand) -> float:
    """How many ₹ one unit of the ad account's currency is (1 for ₹ accounts; Goals page sets the rate otherwise)."""
    if currency(brand) == "INR":
        return 1.0
    from app import goals
    return float(goals.load(brand)["business"].get("fx_rate_to_inr") or 88)


def discover(brand: Brand) -> list[dict]:
    """Ad accounts the token can see. If exactly one is named like the brand, it's chosen automatically."""
    try:
        body = publisher._call("GET", f"{publisher._base(brand)}/me/adaccounts", _token(brand),
                               fields="name,account_id,account_status,currency", limit="100")
    except publisher.PublishError as e:
        if "permission" in str(e).lower() or "token" in str(e).lower():
            raise AdsError(ACCESS_HELP.format(name=account_id(brand) or 'the one you use')) from None
        raise AdsError(str(e)) from None
    accounts = [{"id": a["account_id"], "name": a.get("name") or a["account_id"], "currency": a.get("currency"),
                 "active": a.get("account_status") == 1} for a in body.get("data", [])]
    set_state(brand.slug, "meta:accounts", json.dumps(accounts))
    matches = [a for a in accounts if a["name"].strip().lower() == brand.name.strip().lower()]
    if len(matches) == 1 and not account_id(brand):
        set_account(brand, matches[0]["id"])
    return accounts


def _call(brand: Brand, path: str, **params) -> dict:
    act = account_id(brand)
    if not act:
        raise AdsError(f"No ad account chosen yet. Open the Ads page and pick the “{brand.name}” ad account.")
    try:
        return publisher._call("GET", f"{publisher._base(brand)}/{act}{path}", _token(brand), **params)
    except publisher.PublishError as e:
        if "permission" in str(e).lower() or "token" in str(e).lower():
            raise AdsError(ACCESS_HELP.format(name=account_id(brand) or 'the one you use')) from None
        raise AdsError(str(e)) from None


def _pick(items: list[dict] | None, names: tuple[str, ...]) -> float:
    found = {a.get("action_type"): float(a.get("value") or 0) for a in items or []}
    return next((found[n] for n in names if n in found), 0.0)


def refresh(brand: Brand, days: int = 30) -> int:
    """Re-read daily results per ad for the last `days` days, plus the current campaign/ad set structure.
    Money is stored in ₹ (converted if the ad account uses another currency)."""
    if not get_state(brand.slug, "meta:currency"):
        read_currency(brand)
    rate = to_inr(brand)
    until = datetime.now(IST).date()
    since = until - timedelta(days=days)
    rows, after = [], None
    for _ in range(40):
        params = {"level": "ad", "fields": ",".join(INSIGHT_FIELDS), "time_increment": "1", "limit": "500",
                  "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()})}
        if after:
            params["after"] = after
        body = _call(brand, "/insights", **params)
        rows += body.get("data", [])
        after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
        if not (body.get("paging") or {}).get("next"):
            break
    with connect() as conn:
        conn.execute("DELETE FROM ads_daily WHERE brand = ? AND day >= ?", (brand.slug, since.isoformat()))
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO ads_daily (brand, day, ad_id, ad_name, adset_id, adset_name, campaign_id, "
                "campaign_name, spend, impressions, reach, frequency, link_clicks, add_to_cart, checkouts, purchases, "
                "purchase_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (brand.slug, r["date_start"], r["ad_id"], r.get("ad_name"), r.get("adset_id"), r.get("adset_name"),
                 r.get("campaign_id"), r.get("campaign_name"), float(r.get("spend") or 0) * rate, int(r.get("impressions") or 0),
                 int(r.get("reach") or 0), float(r.get("frequency") or 0), int(r.get("inline_link_clicks") or 0),
                 int(_pick(r.get("actions"), ADD_TO_CART)), int(_pick(r.get("actions"), CHECKOUT)),
                 int(_pick(r.get("actions"), PURCHASE)), _pick(r.get("action_values"), PURCHASE) * rate))
    structure(brand, refresh_now=True)
    set_state(brand.slug, "meta:last_refresh", datetime.now(IST).isoformat(timespec="seconds"))
    log.info("Meta ads refreshed: %d ad-days since %s", len(rows), since)
    return len(rows)


def structure(brand: Brand, refresh_now: bool = False) -> dict:
    """Campaigns → ad sets (status, daily budget) → ads, as last read from Meta."""
    if not refresh_now:
        return json.loads(get_state(brand.slug, "meta:structure") or '{"adsets": [], "ads": []}')
    adsets = _call(brand, "/adsets", fields="id,name,status,effective_status,daily_budget,campaign_id,"
                                            "campaign{name,objective,status}", limit="200").get("data", [])
    ads = _call(brand, "/ads", fields="id,name,status,effective_status,adset_id,creative{id}",
                limit="300").get("data", [])
    out = {"read_at": datetime.now(IST).isoformat(timespec="seconds"),
           "adsets": [{"id": a["id"], "name": a.get("name"), "status": a.get("effective_status") or a.get("status"),
                       # Meta gives budgets in the currency's smallest unit (paise for INR)
                       "daily_budget": round(int(a["daily_budget"]) / 100 * to_inr(brand), 2)
                       if a.get("daily_budget") else None,
                       "campaign_id": a.get("campaign_id"), "campaign": (a.get("campaign") or {}).get("name"),
                       "objective": (a.get("campaign") or {}).get("objective")} for a in adsets],
           "ads": [{"id": a["id"], "name": a.get("name"), "status": a.get("effective_status") or a.get("status"),
                    "adset_id": a.get("adset_id"), "creative_id": (a.get("creative") or {}).get("id")} for a in ads]}
    set_state(brand.slug, "meta:structure", json.dumps(out))
    return out


def active_daily_budget(brand: Brand) -> float:
    """Sum of daily budgets of ad sets that are running now (₹)."""
    return round(sum(a["daily_budget"] or 0 for a in structure(brand)["adsets"] if a["status"] == "ACTIVE"), 2)


# ---------------------------------------------------------------- numbers

def _sum(rows: list[dict]) -> dict:
    spend = sum(r["spend"] or 0 for r in rows)
    imp = sum(r["impressions"] or 0 for r in rows)
    clicks = sum(r["link_clicks"] or 0 for r in rows)
    buys = sum(r["purchases"] or 0 for r in rows)
    value = sum(r["purchase_value"] or 0 for r in rows)
    reach = sum(r["reach"] or 0 for r in rows)
    return {"spend": round(spend, 2), "impressions": imp, "link_clicks": clicks, "purchases": buys,
            "purchase_value": round(value, 2), "add_to_cart": sum(r["add_to_cart"] or 0 for r in rows),
            "checkouts": sum(r["checkouts"] or 0 for r in rows),
            "roas": round(value / spend, 2) if spend else None, "cpa": round(spend / buys, 2) if buys else None,
            "link_ctr": round(100 * clicks / imp, 2) if imp else None, "cpc": round(spend / clicks, 2) if clicks else None,
            "cpm": round(1000 * spend / imp, 2) if imp else None,
            "frequency": round(imp / reach, 2) if reach else None}  # approximate over several days


def _rows(brand: Brand, start: date, end: date) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ads_daily WHERE brand = ? AND day BETWEEN ? AND ?",
                                              (brand.slug, start.isoformat(), end.isoformat()))]


def totals(brand: Brand, days: int = 7, end: date | None = None) -> dict:
    end = end or (datetime.now(IST).date() - timedelta(days=1))
    start = end - timedelta(days=days - 1)
    return {"start": start.isoformat(), "end": end.isoformat(), **_sum(_rows(brand, start, end))}


def by(brand: Brand, level: str = "adset", days: int = 7) -> list[dict]:
    """Per ad set (or ad): this period vs the period before, so trends (fatigue, winners) show."""
    key = {"adset": ("adset_id", "adset_name"), "ad": ("ad_id", "ad_name"),
           "campaign": ("campaign_id", "campaign_name")}[level]
    end = datetime.now(IST).date() - timedelta(days=1)
    now_rows = _rows(brand, end - timedelta(days=days - 1), end)
    before = _rows(brand, end - timedelta(days=2 * days - 1), end - timedelta(days=days))
    out = []
    for ident in sorted({r[key[0]] for r in now_rows}):
        mine = [r for r in now_rows if r[key[0]] == ident]
        prev = _sum([r for r in before if r[key[0]] == ident])
        cur = _sum(mine)
        out.append({"id": ident, "name": mine[0][key[1]], **cur, "prev": prev,
                    "adset_id": mine[0]["adset_id"], "campaign_name": mine[0]["campaign_name"],
                    "max_daily_frequency": max((r["frequency"] or 0) for r in mine)})
    return sorted(out, key=lambda x: x["spend"], reverse=True)


def daily(brand: Brand, days: int = 14) -> list[dict]:
    end = datetime.now(IST).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = end - timedelta(days=i)
        out.append({"day": d.isoformat(), **_sum(_rows(brand, d, d))})
    return out


def month_spend(brand: Brand) -> float:
    today = datetime.now(IST).date()
    return _sum(_rows(brand, today.replace(day=1), today))["spend"]


def summary_text(brand: Brand) -> str:
    """Plain text for the agents' prompts (numbers only from Meta)."""
    if not account_id(brand):
        return "Meta ads: no ad account chosen yet."
    t7, t28 = totals(brand, 7), totals(brand, 28)
    lines = [f"Meta ads (ad account {account_id(brand)}), last 7 days {t7['start']}–{t7['end']}: "
             + json.dumps({k: t7[k] for k in ('spend', 'purchases', 'purchase_value', 'roas', 'cpa', 'link_ctr',
                                              'cpc', 'cpm')}),
             "Last 28 days: " + json.dumps({k: t28[k] for k in ('spend', 'purchases', 'roas', 'cpa', 'link_ctr')}),
             f"Running ad sets now (daily budget ₹): " + json.dumps(
                 [{"id": a["id"], "name": a["name"], "budget": a["daily_budget"], "campaign": a["campaign"]}
                  for a in structure(brand)["adsets"] if a["status"] == "ACTIVE"]),
             "By ad set, last 7 days vs the 7 before: " + json.dumps(
                 [{"id": a["id"], "name": a["name"], "spend": a["spend"], "roas": a["roas"], "cpa": a["cpa"],
                   "ctr": a["link_ctr"], "prev_roas": a["prev"]["roas"], "prev_ctr": a["prev"]["link_ctr"],
                   "max_daily_frequency": a["max_daily_frequency"]} for a in by(brand, "adset")]),
             "By ad, last 7 days: " + json.dumps(
                 [{"id": a["id"], "name": a["name"], "adset_id": a["adset_id"], "spend": a["spend"],
                   "roas": a["roas"], "ctr": a["link_ctr"], "prev_ctr": a["prev"]["link_ctr"],
                   "max_daily_frequency": a["max_daily_frequency"]} for a in by(brand, "ad")][:25])]
    return "\n".join(lines)
