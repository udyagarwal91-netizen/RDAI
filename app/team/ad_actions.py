"""Carrying out an ad change you approved (Meta Marketing API v26.0) — only ever on the brand's own ad account.

- Pause / switch on / change daily budget: done straight away after your ✅ (limits re-checked first).
- New retargeting ad set or boosted post: created PAUSED, then you get a second "Switch on?" ✅ after you've
  had a chance to look at it in Ads Manager. So nothing new starts spending on a single tap.
Needs the ads_management permission and "Manage campaigns" on the ad account for the system user.
"""

import json
import logging
from datetime import datetime, timedelta

from app import goals, meta_ads, publisher
from app.brands import Brand
from app.db import get_state
from app.team import proposals, timed

log = logging.getLogger(__name__)
PIXEL_EVENTS = {"checkout_14d": ("InitiateCheckout", 14), "cart_14d": ("AddToCart", 14),
                "visitors_30d": ("PageView", 30)}
MANAGE_HELP = ("Meta refused the change. The System User needs the ads_management permission and “Manage campaigns” "
               "on the ad account (business.facebook.com → Business Settings → Accounts → Ad accounts → {name} → "
               "Assign people). Meta said: {err}")


class ActionError(Exception):
    pass


def _post(brand: Brand, path: str, **params) -> dict:
    token = brand.secret("META_ACCESS_TOKEN")
    try:
        return publisher._call("POST", f"{publisher._base(brand)}/{path}", token, **params)
    except publisher.PublishError as e:
        raise ActionError(MANAGE_HELP.format(name=brand.name, err=e)) from None


def _act(brand: Brand) -> str:
    act = meta_ads.account_id(brand)
    if not act:
        raise ActionError("No ad account chosen yet (Ads page).")
    return act


def _belongs(brand: Brand, object_id: str) -> bool:
    """Only touch ad sets/ads of the brand's own ad account (as last read from it)."""
    s = meta_ads.structure(brand)
    return object_id in {a["id"] for a in s["adsets"]} | {a["id"] for a in s["ads"]} | \
        set(json.loads(get_state(brand.slug, "meta:created_ids") or "[]"))


def _remember_created(brand: Brand, *ids: str) -> None:
    from app.db import set_state
    known = json.loads(get_state(brand.slug, "meta:created_ids") or "[]")
    set_state(brand.slug, "meta:created_ids", json.dumps(known + [i for i in ids if i]))


def _pixel(brand: Brand) -> str:
    body = publisher._call("GET", f"{publisher._base(brand)}/{_act(brand)}/adspixels",
                           brand.secret("META_ACCESS_TOKEN"), fields="id,name")
    pixels = body.get("data") or []
    if not pixels:
        raise ActionError("This ad account has no Meta Pixel, so retargeting audiences can't be made. Connect the "
                          "Pixel (Shopify → Facebook & Instagram app) first.")
    return pixels[0]["id"]


def _audience(brand: Brand, kind: str, pixel: str) -> str:
    if kind not in PIXEL_EVENTS:
        raise ActionError("That audience type has to be made in Ads Manager for now (only website-visitor, "
                          "add-to-cart and checkout audiences are automatic).")
    event, days = PIXEL_EVENTS[kind]
    src = [{"id": pixel, "type": "pixel"}]
    rule = {"inclusions": {"operator": "or", "rules": [{
                "event_sources": src, "retention_seconds": days * 86400,
                "filter": {"operator": "and", "filters": [{"field": "event", "operator": "eq", "value": event}]}}]},
            "exclusions": {"operator": "or", "rules": [{
                "event_sources": src, "retention_seconds": 180 * 86400,
                "filter": {"operator": "and", "filters": [{"field": "event", "operator": "eq", "value": "Purchase"}]}}]}}
    body = _post(brand, f"{_act(brand)}/customaudiences", name=f"{brand.name} · {proposals.AUDIENCES[kind]}"[:120],
                 rule=json.dumps(rule), prefill="true")
    return body["id"]


def _campaign(brand: Brand, name: str, objective: str) -> str:
    return _post(brand, f"{_act(brand)}/campaigns", name=name, objective=objective, status="PAUSED",
                 special_ad_categories="[]", is_adset_budget_sharing_enabled="false")["id"]


def _paise(rupees: float) -> str:
    return str(int(round(float(rupees) * 100)))  # Meta wants budgets in paise


@timed("smo", "Making an approved ad change")
def execute(brand: Brand, pid: int) -> str:
    """Do one approved proposal. Returns a plain-English result; raises ActionError on failure."""
    p = proposals.get(brand, pid)
    if not p or p["status"] != "proposed":
        raise ActionError("That ad change was already handled.")
    action, target, params = p["action"], p["target_id"], p["params"]
    if action in ("budget", "retarget", "boost", "activate"):
        if action == "budget" and float(params["to"]) > float(params["from"]) and \
                get_state(brand.slug, "finance:tracking_suspect"):
            raise ActionError("Budget raises are frozen: Meta and Shopify sales don't match, so tracking may be broken.")
        try:
            meta_ads.structure(brand, refresh_now=True)
        except meta_ads.AdsError as e:
            raise ActionError(str(e)) from None
        if action == "budget":  # use the current budget, not the one from when it was proposed
            current = next((a["daily_budget"] for a in meta_ads.structure(brand)["adsets"] if a["id"] == target), None)
            params = {**params, "from": current or params["from"]}
        try:
            params = proposals.check(brand, action, params)
        except proposals.GuardrailError as e:
            raise ActionError(f"Not done — {e}") from None
    if action in ("pause", "activate", "budget") and not _belongs(brand, target):
        raise ActionError("That ad set/ad isn't in the brand's ad account, so it wasn't touched.")

    if action == "pause":
        _post(brand, target, status="PAUSED")
        return f"Paused “{p['target_name']}”."
    if action == "activate":
        _post(brand, target, status="ACTIVE")
        return f"Switched on “{p['target_name']}”. It starts spending within minutes."
    if action == "budget":
        _post(brand, target, daily_budget=_paise(params["to"]))
        return f"Daily budget of “{p['target_name']}” is now ₹{params['to']:g}."

    stamp = datetime.now().strftime("%d %b")
    if action == "retarget":
        pixel = _pixel(brand)
        audience = _audience(brand, params.get("audience") or "checkout_14d", pixel)
        campaign = _campaign(brand, f"Team · Retargeting · {stamp}", "OUTCOME_SALES")
        adset = _post(brand, f"{_act(brand)}/adsets", name=f"Retargeting · {proposals.AUDIENCES.get(params.get('audience'), '')}"[:100],
                      campaign_id=campaign, daily_budget=_paise(params["daily_budget"]), billing_event="IMPRESSIONS",
                      optimization_goal="OFFSITE_CONVERSIONS", bid_strategy="LOWEST_COST_WITHOUT_CAP",
                      promoted_object=json.dumps({"pixel_id": pixel, "custom_event_type": "PURCHASE"}),
                      targeting=json.dumps({"geo_locations": {"countries": ["IN"]}, "age_min": 18,
                                            "custom_audiences": [{"id": audience}]}), status="PAUSED")["id"]
        creative = next((a["creative_id"] for a in meta_ads.structure(brand)["ads"] if a["id"] == target), None)
        ad = None
        if creative:
            ad = _post(brand, f"{_act(brand)}/ads", name="Retargeting · best ad", adset_id=adset,
                       creative=json.dumps({"creative_id": creative}), status="PAUSED")["id"]
        _remember_created(brand, campaign, adset, ad)
        _follow_up(brand, adset, f"Retargeting ad set ({stamp})", params["daily_budget"])
        return ("Created a retargeting ad set, PAUSED, with ₹{:g}/day{}. Look at it in Ads Manager, then approve "
                "“Switch on” on Telegram.").format(params["daily_budget"], "" if ad else
                                                    " (no ad inside yet — add one in Ads Manager)")
    if action == "boost":
        ig_id, page_id = brand.config["meta"].get("instagram_account_id"), brand.config["meta"].get("facebook_page_id")
        campaign = _campaign(brand, f"Team · Boost · {stamp}", "OUTCOME_ENGAGEMENT")
        end = (datetime.now() + timedelta(days=int(params.get("days") or 5))).strftime("%Y-%m-%dT%H:%M:%S+0530")
        adset = _post(brand, f"{_act(brand)}/adsets", name=f"Boost · {p['target_name']}"[:100], campaign_id=campaign,
                      daily_budget=_paise(params["daily_budget"]), billing_event="IMPRESSIONS",
                      optimization_goal="POST_ENGAGEMENT", bid_strategy="LOWEST_COST_WITHOUT_CAP", end_time=end,
                      targeting=json.dumps({"geo_locations": {"countries": ["IN"]}, "age_min": 18}),
                      status="PAUSED")["id"]
        creative = _post(brand, f"{_act(brand)}/adcreatives", name=f"Boost · {p['target_name']}"[:100],
                         object_id=page_id, instagram_user_id=ig_id, source_instagram_media_id=target)["id"]
        ad = _post(brand, f"{_act(brand)}/ads", name="Boosted post", adset_id=adset,
                   creative=json.dumps({"creative_id": creative}), status="PAUSED")["id"]
        _remember_created(brand, campaign, adset, ad)
        _follow_up(brand, adset, f"Boost of “{p['target_name']}”", params["daily_budget"])
        return (f"Created the boost, PAUSED, with ₹{params['daily_budget']:g}/day until {end[:10]}. Approve "
                "“Switch on” on Telegram when you're happy.")
    raise ActionError("Unknown ad change.")


def _follow_up(brand: Brand, adset_id: str, name: str, budget: float) -> None:
    pid, msg = proposals.create(brand, "smo", "activate", adset_id, name, {"daily_budget": budget},
                                "Created paused for you to check first.", "Starts spending when switched on.")
    if pid and msg == "Proposed.":
        proposals.ask_owner(brand, pid)


def decide(brand: Brand, pid: int, yes: bool) -> str:
    """Your answer to a proposal (from Telegram or the Ads page)."""
    p = proposals.get(brand, pid)
    if not p:
        return "That ad change doesn't exist."
    if p["status"] != "proposed":
        return f"Already {p['status']}."
    if not yes:
        proposals.set_status(brand, pid, "rejected", "Owner said no.")
        return "OK — not doing it."
    try:
        result = execute(brand, pid)
    except ActionError as e:
        proposals.set_status(brand, pid, "failed", str(e))
        log.warning("Ad change #%d failed: %s", pid, e)
        return f"⚠️ {e}"
    proposals.set_status(brand, pid, "done", result)
    log.info("Ad change #%d done: %s", pid, result)
    return f"✅ {result}"
