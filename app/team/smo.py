"""📈 SMO: reads ad and post results and says what to stop, scale or fix.

Daily (no AI cost): clear losers and tired ads are spotted with fixed rules and proposed for pausing — each
proposal waits for your ✅. Weekly: a written brief with findings, organic learnings and proposals.
Nothing is judged on too little data (fewer than 3 days, or less than 2× the target cost per purchase spent).
"""

import logging

from app import claude, goals, insights, meta_ads
from app.brands import Brand
from app.team import AGENTS, post_brief, proposals, skill

log = logging.getLogger(__name__)


def _targets(brand: Brand) -> tuple[float, float]:
    prog = {p["key"]: p for p in goals.progress(brand)}
    cpa = (prog.get("cpa") or {}).get("target_now") or 60
    roas = (prog.get("meta_roas") or {}).get("target_now") or 5
    return float(cpa), float(roas)


def findings(brand: Brand) -> dict:
    """Rule-based checks on the last 7 days vs the 7 before, per ad set and per ad."""
    target_cpa, target_roas = _targets(brand)
    out = {"losers": [], "tired": [], "winners": [], "target_cpa": target_cpa, "target_roas": target_roas}
    running = {a["id"] for a in meta_ads.structure(brand)["ads"] if a["status"] == "ACTIVE"} | \
              {a["id"] for a in meta_ads.structure(brand)["adsets"] if a["status"] == "ACTIVE"}
    for level in ("adset", "ad"):
        for x in meta_ads.by(brand, level):
            if x["id"] not in running or x["spend"] <= 0:
                continue
            item = {"level": level, "id": x["id"], "name": x["name"], "spend": x["spend"], "roas": x["roas"],
                    "purchases": x["purchases"], "ctr": x["link_ctr"], "prev_ctr": x["prev"]["link_ctr"],
                    "frequency": x["max_daily_frequency"]}
            enough = x["spend"] >= 2 * target_cpa
            if enough and (not x["purchases"] or (x["roas"] or 0) < 0.5 * target_roas):
                out["losers"].append(item)
            elif x["max_daily_frequency"] > 3 or (x["prev"]["link_ctr"] and x["link_ctr"] is not None and
                                                  x["spend"] >= target_cpa and
                                                  x["link_ctr"] < 0.75 * x["prev"]["link_ctr"]):
                out["tired"].append(item)
            elif enough and (x["roas"] or 0) >= target_roas:
                out["winners"].append(item)
    return out


def daily_tick(brand: Brand) -> list[int]:
    """Propose pausing clear losers (one ✅ each). Returns new proposal ids."""
    new = []
    f = findings(brand)
    for x in f["losers"]:
        pid, msg = proposals.create(
            brand, "smo", "pause", x["id"], x["name"], {"level": x["level"]},
            f"Spent ₹{x['spend']:g} in 7 days with {x['purchases']} sale(s) (ROAS {x['roas'] or 0}); target cost per "
            f"purchase is ₹{f['target_cpa']:g}.", "Stops money going to an ad that isn't selling.")
        if pid and msg == "Proposed.":
            proposals.ask_owner(brand, pid)
            new.append(pid)
    return new


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "finding": {"type": "string"}, "numbers": {"type": "string"}, "action": {"type": "string"}},
            "required": ["finding", "numbers", "action"], "additionalProperties": False}},
        "organic": {"type": "array", "items": {"type": "string"}},
        "new_creatives_needed": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "findings", "organic", "new_creatives_needed"], "additionalProperties": False,
}


def weekly_brief(brand: Brand, client=None) -> dict | None:
    has_ads = bool(meta_ads.account_id(brand)) and meta_ads.totals(brand, 7)["spend"] > 0
    rep = insights.report(brand, 7)
    if not has_ads and not rep["rows"]:
        return None
    client = client or claude.client_for(brand)
    f = findings(brand) if has_ads else {}
    system = f"You are the {AGENTS['smo']['name']} agent of {brand.name}'s team.\n\n{skill(brand, 'smo')}"
    user = (f"Write this week's results brief for the Manager.\n\nGOALS:\n{goals.summary_for_agents(brand)}\n\n"
            f"ADS:\n{meta_ads.summary_text(brand) if has_ads else 'No ad spend this week.'}\n\n"
            f"RULE CHECKS (losers / tired / winners): {f}\n\n"
            f"OUR POSTS (organic, last 7 days):\n{insights.numbers_text(rep) if rep['rows'] else 'No posts yet.'}")
    data, cost, _ = claude.ask(client, system, [{"role": "user", "content": user}], BRIEF_SCHEMA, effort="medium")
    body = "\n".join([data["headline"], "",
                      *[f"• {x['finding']} ({x['numbers']})\n  → {x['action']}" for x in data["findings"]],
                      *(["", "Posts:"] + [f"- {o}" for o in data["organic"]] if data["organic"] else []),
                      *(["", "New creatives needed:"] + [f"- {c}" for c in data["new_creatives_needed"]]
                        if data["new_creatives_needed"] else [])])
    brief_id = post_brief(brand, "smo", "smo", data["headline"][:200], body, {**data, "rules": f}, cost)
    if has_ads:
        daily_tick(brand)
    return {"id": brief_id, "data": data, "cost_usd": cost, "body": body}
