"""🗺 Planner as campaign architect: the weekly Meta ads plan — funnel layers (new buyers, boosts, warm and hot
retargeting, past customers), the story sequence people see, the follow-up on other channels, and concrete
proposals (new retargeting ad set, boost a proven post, budget moves) that each wait for your ✅.

Budgets always fit your daily and monthly limits (checked in code, not left to the AI).
"""

import logging

from app import claude, goals, insights, meta_ads
from app.brands import Brand
from app.team import AGENTS, latest, post_brief, proposals, say, skill

log = logging.getLogger(__name__)
LAYERS = ["prospecting", "warm_boost", "retarget_warm", "retarget_hot", "customers"]

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "layers": {"type": "array", "items": {"type": "object", "properties": {
            "layer": {"type": "string", "enum": LAYERS}, "purpose": {"type": "string"},
            "audience": {"type": "string"}, "daily_budget": {"type": "number"},
            "creatives": {"type": "string"}, "success_metric": {"type": "string"}, "stop_rule": {"type": "string"}},
            "required": ["layer", "purpose", "audience", "daily_budget", "creatives", "success_metric", "stop_rule"],
            "additionalProperties": False}},
        "sequence": {"type": "array", "items": {"type": "object", "properties": {
            "when": {"type": "string"}, "message": {"type": "string"}, "format": {"type": "string"}},
            "required": ["when", "message", "format"], "additionalProperties": False}},
        "cross_channel": {"type": "array", "items": {"type": "object", "properties": {
            "channel": {"type": "string", "enum": ["instagram_organic", "facebook_organic", "whatsapp",
                                                   "youtube_shorts", "google_remarketing", "email"]},
            "idea": {"type": "string"}, "when": {"type": "string", "enum": ["now", "later"]}},
            "required": ["channel", "idea", "when"], "additionalProperties": False}},
        "proposals": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["retarget", "boost", "budget", "pause", "activate"]},
            "target_id": {"type": "string", "description": "Ad set / ad id for budget, pause, activate; the "
                                                           "Instagram media id for boost; the ad id whose creative "
                                                           "to reuse for retarget; else empty."},
            "target_name": {"type": "string"},
            "audience": {"type": "string", "enum": list(proposals.AUDIENCES)},
            "daily_budget": {"type": "number", "description": "₹ per day (new daily budget for 'budget')."},
            "reason": {"type": "string"}, "expected": {"type": "string"}},
            "required": ["action", "target_id", "target_name", "audience", "daily_budget", "reason", "expected"],
            "additionalProperties": False}},
    },
    "required": ["headline", "layers", "sequence", "cross_channel", "proposals"],
    "additionalProperties": False,
}


def _boost_candidates(brand: Brand) -> list[dict]:
    """Our own published Instagram posts that beat the average (saves + shares first)."""
    rows = [r for r in insights.latest(brand, 30) if r["platform"] == "instagram" and r.get("platform_post_id")]
    rows.sort(key=lambda r: ((r["saves"] or 0) + (r["shares"] or 0), r["reach"] or 0), reverse=True)
    return [{"ig_media_id": r["platform_post_id"], "when": f"{r['when']:%d %b}", "format": r["format"],
             "reach": r["reach"], "saves": r["saves"], "shares": r["shares"], "caption": (r["caption"] or "")[:100]}
            for r in rows[:5]]


def plan(brand: Brand, client=None) -> dict | None:
    """Write the campaign plan and store its proposals. None if there's no ad account to plan for yet."""
    if not meta_ads.account_id(brand):
        log.info("No ad account chosen yet; skipping the campaign plan")
        return None
    client = client or claude.client_for(brand)
    g = goals.load(brand)["guardrails"]
    known = meta_ads.structure(brand)
    direction, market = latest(brand, "direction"), latest(brand, "market")
    system = (f"You are the {AGENTS['planner']['name']} of {brand.name}'s team, acting as campaign architect for "
              f"Meta ads. Follow your playbook.\n\n{skill(brand, 'planner')}\n\nBRAND RULES:\n{brand.rules}")
    user = (f"Plan this week's Meta ads.\n\nGOALS AND LIMITS:\n{goals.summary_for_agents(brand)}\n\n"
            f"CURRENT ADS:\n{meta_ads.summary_text(brand)}\n\n"
            f"ALL AD SETS AND ADS (ids you may refer to): "
            f"{[{k: a[k] for k in ('id', 'name', 'status', 'daily_budget')} for a in known['adsets']]} / "
            f"{[{k: a[k] for k in ('id', 'name', 'status', 'adset_id')} for a in known['ads']][:30]}\n\n"
            f"OUR BEST RECENT INSTAGRAM POSTS (boost candidates): {_boost_candidates(brand)}\n\n"
            f"MANAGER'S DIRECTION: {direction['body'] if direction else 'none yet'}\n\n"
            f"MARKET BRIEF: {market['body'][:3000] if market else 'none yet'}\n\n"
            f"Rules: all layer budgets together must fit ₹{g['daily_ad_budget']} a day. Proposals only for changes "
            f"worth making now (max 4); ids must come from the lists above; budget changes give the NEW daily budget. "
            f"If there's not enough data, propose less and say so.")
    data, cost, _ = claude.ask(client, system, [{"role": "user", "content": user}], SCHEMA, effort="high")

    total = sum(max(0.0, layer["daily_budget"]) for layer in data["layers"])
    cap = float(g["daily_ad_budget"])
    if total > cap and total > 0:  # keep the plan inside the owner's limit, whatever the AI suggested
        for layer in data["layers"]:
            layer["daily_budget"] = round(layer["daily_budget"] * cap / total)
    ids = {a["id"]: a for a in known["adsets"]} | {a["id"]: a for a in known["ads"]}
    made, refused = [], []
    for p in data["proposals"]:
        action, target = p["action"], p["target_id"].strip()
        if action in ("budget", "pause", "activate") and target not in ids:
            refused.append(f"{p['target_name']}: unknown ad set/ad")
            continue
        if action == "budget" and target not in {a["id"] for a in known["adsets"]}:
            refused.append(f"{p['target_name']}: budgets are set on ad sets")
            continue
        params = {"audience": p["audience"], "daily_budget": p["daily_budget"]}
        if action == "budget":
            params = {"from": ids[target]["daily_budget"] or 0, "to": p["daily_budget"]}
        pid, msg = proposals.create(brand, "planner", action, target, p["target_name"], params, p["reason"],
                                    p["expected"])
        (made if pid else refused).append(pid if pid else f"{p['target_name']}: {msg}")
    body = _body(data, refused)
    brief_id = post_brief(brand, "planner", "campaign_plan", data["headline"][:200], body,
                          {**data, "proposal_ids": made, "refused": refused}, cost)
    from app.telegram import esc
    say(brand, "planner", f"Ad plan: <b>{esc(data['headline'])}</b>\n"
                          + "\n".join(f"• {esc(layer['layer'].replace('_', ' '))}: ₹{layer['daily_budget']:g}/day — "
                                      f"{esc(layer['purpose'])}" for layer in data["layers"])
                          + ("\n<i>Ad changes below need your ✅.</i>" if made else ""))
    for pid in made:
        proposals.ask_owner(brand, pid)
    log.info("Campaign plan written: %d proposals, %d refused (about US$%.2f)", len(made), len(refused), cost)
    return {"id": brief_id, "data": data, "cost_usd": cost, "proposals": made, "refused": refused}


def _body(data: dict, refused: list[str]) -> str:
    lines = [data["headline"], "", "Funnel layers:"]
    lines += [f"- {layer['layer'].replace('_', ' ')} (₹{layer['daily_budget']:g}/day): {layer['purpose']} · "
              f"audience: {layer['audience']} · creatives: {layer['creatives']} · success: {layer['success_metric']}"
              f" · stop: {layer['stop_rule']}" for layer in data["layers"]]
    if data["sequence"]:
        lines += ["", "Story sequence:"] + [f"- {s['when']}: {s['message']} ({s['format']})" for s in data["sequence"]]
    if data["cross_channel"]:
        lines += ["", "Other channels:"] + [f"- {c['channel'].replace('_', ' ')} ({c['when']}): {c['idea']}"
                                             for c in data["cross_channel"]]
    if data["proposals"]:
        lines += ["", "Proposed ad changes (each needs your ✅):"] + [
            f"- {p['action']}: {p['target_name']} — {p['reason']}" for p in data["proposals"]]
    if refused:
        lines += ["", "Not proposed (safety limits or bad ids):"] + [f"- {r}" for r in refused]
    return "\n".join(lines)
