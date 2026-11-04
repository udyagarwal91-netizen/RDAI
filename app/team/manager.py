"""🧠 Manager: reads every brief (market, results, money), decides the week's direction, logs decisions with a
date to check them, judges old decisions honestly, and writes the Monday report for you.

The Manager decides content direction itself (you still approve every post). Anything that spends money
becomes a proposal waiting for your ✅ — the Manager can't spend.
"""

import json
import logging
from datetime import datetime

from app import claude, goals, insights
from app.brands import Brand
from app.db import set_state
from app.team import (AGENTS, add_decision, decisions, latest, post_brief, review_decision, say, skill)

log = logging.getLogger(__name__)

DIRECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "This week's direction in one sentence."},
        "priorities": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
            "priority": {"type": "string"}, "why": {"type": "string"}},
            "required": ["priority", "why"], "additionalProperties": False}},
        "plan_instructions": {"type": "string",
                              "description": "Exact instructions for the Content Planner's weekly plan."},
        "creator_notes": {"type": "string", "description": "Guidance for the Content Creator's hooks and visuals."},
        "decisions": {"type": "array", "items": {"type": "object", "properties": {
            "decision": {"type": "string"}, "reason": {"type": "string"},
            "expected_result": {"type": "string"}, "check_after_days": {"type": "integer"}},
            "required": ["decision", "reason", "expected_result", "check_after_days"], "additionalProperties": False}},
        "reviews": {"type": "array", "items": {"type": "object", "properties": {
            "decision_id": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["worked", "did_not_work", "unclear"]},
            "what_we_learned": {"type": "string"}},
            "required": ["decision_id", "verdict", "what_we_learned"], "additionalProperties": False}},
        "asks_for_owner": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
    },
    "required": ["headline", "priorities", "plan_instructions", "creator_notes", "decisions", "reviews",
                 "asks_for_owner"],
    "additionalProperties": False,
}


def _system(brand: Brand) -> str:
    return (f"You are the {AGENTS['manager']['name']} of the social media team for {brand.name}. "
            f"Follow your playbook.\n\n{skill(brand, 'manager')}\n\nBRAND RULES:\n{brand.rules}")


def team_inputs(brand: Brand) -> str:
    """Everything the Manager reads: goals, the latest briefs from each agent, lessons and open decisions."""
    parts = [f"GOALS AND SAFETY LIMITS (the only targets that exist):\n{goals.summary_for_agents(brand)}"]
    for kind, who in (("market", "researcher"), ("smo", "smo"), ("finance", "finance"),
                      ("campaign_plan", "planner"), ("direction", "manager")):
        b = latest(brand, kind, max_age_days=14)
        label = f"{AGENTS[who]['icon']} LATEST {kind.upper().replace('_', ' ')} BRIEF"
        parts.append(f"{label} ({b['when']}):\n{b['body']}" if b else f"{label}: none yet.")
    les = insights.lessons(brand)
    parts.append("LESSONS FROM OUR OWN POSTS: " + (json.dumps(les, ensure_ascii=False) if les else "not enough data yet."))
    due = decisions(brand, due_only=True)
    parts.append("DECISIONS DUE FOR REVIEW (judge each one):\n" + ("\n".join(
        f"- id {d['id']}: {d['decision']} (expected: {d['expected']}; made {d['created_at'][:10]})" for d in due)
        if due else "none"))
    recent = [d for d in decisions(brand, 10) if d["status"] != "open"][:5]
    if recent:
        parts.append("RECENTLY JUDGED DECISIONS:\n" + "\n".join(
            f"- {d['decision']} → {d['status']}: {d['outcome']}" for d in recent))
    return "\n\n".join(parts)


def direct(brand: Brand, client=None) -> dict:
    """Decide this week's direction. Stores the brief and decisions; returns the direction."""
    client = client or claude.client_for(brand)
    today = datetime.now().strftime("%A %d %B %Y")
    user = (f"Today is {today}. Decide this week's direction for the team.\n\n{team_inputs(brand)}\n\n"
            "Rules: at most 3 priorities; only numbers that appear above; decisions need a check date; "
            "content direction is yours to decide; anything that spends money goes in asks_for_owner.")
    data, cost, _ = claude.ask(client, _system(brand), [{"role": "user", "content": user}], DIRECTION_SCHEMA,
                               effort="high")
    due_ids = {d["id"] for d in decisions(brand, due_only=True)}
    for r in data["reviews"]:
        if r["decision_id"] in due_ids:
            review_decision(brand, r["decision_id"], r["verdict"], r["what_we_learned"])
    body = _body(data)
    brief_id = post_brief(brand, "manager", "direction", data["headline"][:200], body, data, cost)
    for d in data["decisions"]:
        add_decision(brand, brief_id, d["decision"], d["reason"], d["expected_result"], d["check_after_days"])
    set_state(brand.slug, "team:creator_notes", data["creator_notes"][:2000])
    log.info("Manager set this week's direction (about US$%.2f)", cost)
    return {"id": brief_id, "data": data, "cost_usd": cost, "body": body}


def _body(data: dict) -> str:
    lines = [data["headline"], ""]
    lines += [f"{n}. {p['priority']} — {p['why']}" for n, p in enumerate(data["priorities"], 1)]
    lines += ["", "For the Planner: " + data["plan_instructions"], "For the Creator: " + data["creator_notes"]]
    if data["decisions"]:
        lines += ["", "Decisions:"] + [f"- {d['decision']} (why: {d['reason']}; expect: {d['expected_result']}; "
                                        f"check in {d['check_after_days']} days)" for d in data["decisions"]]
    if data["reviews"]:
        lines += ["", "Old decisions judged:"] + [f"- #{r['decision_id']}: {r['verdict'].replace('_', ' ')} — "
                                                   f"{r['what_we_learned']}" for r in data["reviews"]]
    if data["asks_for_owner"]:
        lines += ["", "Needs you:"] + [f"- {a}" for a in data["asks_for_owner"]]
    return "\n".join(lines)


def tell_owner(brand: Brand, result: dict) -> None:
    from app.telegram import esc
    d = result["data"]
    pri = "\n".join(f"{n}. {esc(p['priority'])}" for n, p in enumerate(d["priorities"], 1))
    asks = ("\n\n<b>Needs you:</b>\n" + "\n".join(f"• {esc(a)}" for a in d["asks_for_owner"])) \
        if d["asks_for_owner"] else ""
    say(brand, "manager", f"This week: <b>{esc(d['headline'])}</b>\n{pri}{asks}\n<i>Details: Team page.</i>")


# ---------------------------------------------------------------- Monday report

REPORT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "wins": {"type": "array", "items": {"type": "string"}},
                   "problems": {"type": "array", "items": {"type": "string"}},
                   "decided": {"type": "array", "items": {"type": "string"}},
                   "needs_from_owner": {"type": "array", "maxItems": 3, "items": {"type": "string"}}},
    "required": ["summary", "wins", "problems", "decided", "needs_from_owner"], "additionalProperties": False,
}


def weekly_report(brand: Brand, client=None) -> dict:
    client = client or claude.client_for(brand)
    user = ("Write the owner's one-page Monday report: goal progress first (use the green/amber/red statuses "
            "given), then wins, problems, what the team decided, and what you need from the owner (max 3). Plain "
            f"words, short lines.\n\n{team_inputs(brand)}")
    data, cost, _ = claude.ask(client, _system(brand), [{"role": "user", "content": user}], REPORT_SCHEMA,
                               effort="medium")
    lights = {"green": "🟢", "amber": "🟡", "red": "🔴"}
    goal_lines = [f"{lights.get(p['status'], '⚪')} {p['label']}: "
                  f"{'—' if p['current'] is None else p['current']} (day 90: {p['final']})"
                  for p in goals.progress(brand)[:6]]
    body = "\n".join([data["summary"], "", *goal_lines, "", "Wins:", *[f"- {w}" for w in data["wins"]],
                      "", "Problems:", *[f"- {p}" for p in data["problems"]],
                      "", "Decided:", *[f"- {d}" for d in data["decided"]],
                      *(["", "Needs you:", *[f"- {n}" for n in data["needs_from_owner"]]]
                        if data["needs_from_owner"] else [])])
    brief_id = post_brief(brand, "manager", "report", "Weekly report", body, data, cost)
    from app.telegram import esc
    say(brand, "manager", "<b>Weekly report</b>\n" + esc(body))
    return {"id": brief_id, "data": data, "cost_usd": cost, "body": body}
