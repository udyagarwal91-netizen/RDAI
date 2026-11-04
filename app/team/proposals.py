"""Ad changes proposed by the team — never made without your ✅.

Every proposal is checked against your safety limits (Goals page) twice: when it's proposed and again at the
moment you approve it (numbers may have moved). A proposal that would break a limit is shrunk to fit or
refused, with the reason shown. Approved actions that create something (a retargeting ad set, a boosted
post) create it PAUSED: you look at it in Ads Manager and switch it on with a second ✅.
"""

import json
import logging
from datetime import datetime, timedelta

from app import goals, meta_ads
from app.brands import Brand
from app.db import connect
from app.team import AGENTS, say

log = logging.getLogger(__name__)
ACTIONS = {"pause": "Pause", "activate": "Switch on", "budget": "Change daily budget",
           "retarget": "New retargeting ad set (created paused)", "boost": "Boost a post (created paused)"}
AUDIENCES = {"checkout_14d": "started checkout in the last 14 days (not bought)",
             "cart_14d": "added to cart in the last 14 days (not bought)",
             "visitors_30d": "visited the website in the last 30 days (not bought)",
             "engaged_30d": "engaged with Instagram or Facebook in the last 30 days",
             "broad": "broad audience in India (Meta finds buyers)"}
EXPIRE_AFTER = timedelta(days=5)


class GuardrailError(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def check(brand: Brand, action: str, params: dict) -> dict:
    """Apply the owner's limits. Returns (possibly reduced) params, or raises GuardrailError with a plain reason."""
    g = goals.load(brand)["guardrails"]
    cap, month_cap = float(g["daily_ad_budget"]), float(g["monthly_ad_budget"])
    running = meta_ads.active_daily_budget(brand)
    params = dict(params)
    if action == "budget":
        old, new = float(params["from"] or 0), float(params["to"] or 0)
        if new > old:
            measured = (goals.measured(brand).get("blended_roas") or {}).get("value")
            if measured is None or measured < float(g["scale_only_if_roas_above"]):
                raise GuardrailError(f"Budgets may only go up while blended ROAS is above "
                                     f"{g['scale_only_if_roas_above']} (it's {measured or 'not measured yet'}).")
            limit = old * (1 + float(g["max_budget_increase_pct"]) / 100)
            if new > limit:
                params["to"] = new = round(limit)
            room = cap - (running - old)
            if new > room:
                if room <= old:
                    raise GuardrailError(f"Your daily ad budget limit is ₹{cap:g} and ₹{running:g} is already running.")
                params["to"] = round(room)
    elif action in ("retarget", "boost"):
        want = float(params.get("daily_budget") or 0)
        room = cap - running
        if room < 50:
            raise GuardrailError(f"No room in your daily ad budget (limit ₹{cap:g}, ₹{running:g} already running). "
                                 "Lower another ad set's budget first, or raise the limit on the Goals page.")
        params["daily_budget"] = round(min(want or room, room))
    if action in ("budget", "retarget", "boost", "activate"):
        spent = meta_ads.month_spend(brand)
        if spent >= month_cap:
            raise GuardrailError(f"This month's ad budget (₹{month_cap:g}) is already used (₹{spent:g}).")
    return params


def create(brand: Brand, agent: str, action: str, target_id: str, target_name: str, params: dict, reason: str,
           expected: str) -> tuple[int | None, str]:
    """Store a proposal (after the limits check). Returns (id, message); id is None if it was refused."""
    if action not in ACTIONS:
        return None, f"Unknown action {action}."
    try:
        params = check(brand, action, params)
    except GuardrailError as e:
        log.info("Proposal refused by safety limits: %s %s — %s", action, target_name, e)
        return None, str(e)
    with connect() as conn:
        dup = conn.execute("SELECT id FROM ad_proposals WHERE brand = ? AND action = ? AND COALESCE(target_id, '') = ? "
                           "AND status = 'proposed'", (brand.slug, action, target_id or "")).fetchone()
        if dup:
            return dup["id"], "Already proposed."
        pid = conn.execute("INSERT INTO ad_proposals (brand, agent, action, target_id, target_name, params, reason, "
                           "expected, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (brand.slug, agent, action, target_id or "", target_name, json.dumps(params), reason,
                            expected, _now())).lastrowid
    return pid, "Proposed."


def describe(p: dict) -> str:
    params = p["params"] if isinstance(p["params"], dict) else json.loads(p["params"] or "{}")
    what = ACTIONS[p["action"]]
    if p["action"] == "budget":
        what += f": ₹{params['from']:g} → ₹{params['to']:g} a day"
    elif p["action"] in ("retarget", "boost"):
        what += f", ₹{params.get('daily_budget', 0):g} a day"
        if params.get("audience"):
            what += f", people who {AUDIENCES.get(params['audience'], params['audience'])}"
    return f"{what} — {p['target_name'] or ''}".strip(" —")


def get(brand: Brand, pid: int) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM ad_proposals WHERE brand = ? AND id = ?", (brand.slug, pid)).fetchone()
    if not r:
        return None
    p = dict(r)
    p["params"] = json.loads(p["params"] or "{}")
    p["summary"] = describe(p)
    p["by"] = f"{AGENTS[p['agent']]['icon']} {AGENTS[p['agent']]['name']}"
    return p


def listing(brand: Brand, status: str | None = None, limit: int = 40) -> list[dict]:
    expire_old(brand)
    sql = "SELECT id FROM ad_proposals WHERE brand = ?" + (" AND status = ?" if status else "") + \
          " ORDER BY id DESC LIMIT ?"
    with connect() as conn:
        ids = [r["id"] for r in conn.execute(sql, (brand.slug, status, limit) if status else (brand.slug, limit))]
    return [get(brand, i) for i in ids]


def expire_old(brand: Brand) -> None:
    cutoff = (datetime.now() - EXPIRE_AFTER).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("UPDATE ad_proposals SET status = 'expired' WHERE brand = ? AND status = 'proposed' "
                     "AND created_at < ?", (brand.slug, cutoff))


def set_status(brand: Brand, pid: int, status: str, result: str = "") -> None:
    with connect() as conn:
        conn.execute("UPDATE ad_proposals SET status = ?, result = ?, decided_at = ? WHERE id = ? AND brand = ?",
                     (status, result[:1000], _now(), pid, brand.slug))


def ask_owner(brand: Brand, pid: int) -> None:
    """Telegram message with ✅ / ❌ for one proposal."""
    from app.telegram import esc
    p = get(brand, pid)
    if not p:
        return
    say(brand, p["agent"], f"💸 <b>Ad change needs your OK</b>\n{esc(p['summary'])}\n<i>Why:</i> {esc(p['reason'])}\n"
                           f"<i>Expect:</i> {esc(p['expected'])}",
        [("✅ Approve", f"ad:y:{pid}"), ("❌ No", f"ad:n:{pid}")])
