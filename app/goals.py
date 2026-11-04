"""The team's goals and hard limits (guardrails) — the numbers every agent works towards.

Starting values come from brands/<brand>/goals.yaml. Once you change anything on
the Goals page, your version is stored in the database and used from then on, so
updates never overwrite your numbers. Agents only ever READ goals; only you change them.

"Current" values come from the latest numbers the Finance and SMO agents measured
(app_state metrics:latest). When a number hasn't been measured yet, it shows
"no data" — the agents are told to say "I don't know yet" instead of guessing.
"""

import json
from datetime import date, datetime

from ruamel.yaml import YAML

from app.brands import Brand
from app.db import get_state, set_state

MILESTONES = (30, 60, 90)
GUARDRAILS = {  # key: (label, unit, default)
    "daily_ad_budget": ("Daily ad budget (all Meta ads)", "₹", 500),
    "monthly_ad_budget": ("Monthly ad budget", "₹", 15000),
    "max_budget_increase_pct": ("Biggest single budget raise", "%", 20),
    "scale_only_if_roas_above": ("Raise budgets only if blended ROAS stays above", "x", 8),
    "ai_cost_monthly": ("Monthly AI cost limit (Claude + Gemini)", "₹", 3000),
}
BUSINESS = {
    "product_cost_pct": ("Product cost, as % of selling price", "%"),
    "shipping_cost_per_order": ("Shipping cost per order", "₹"),
    "payment_fee_pct": ("Payment gateway fee", "%"),
    "cod_share_pct": ("Orders paid cash on delivery (COD)", "%"),
    "rto_pct": ("COD orders refused / returned (RTO)", "%"),
}


def _defaults(brand: Brand) -> dict:
    path = brand.folder / "goals.yaml"
    doc = YAML(typ="safe").load(path.read_text(encoding="utf-8")) if path.exists() else {}
    doc = doc or {}
    doc.setdefault("start_date", date.today().isoformat())
    doc.setdefault("baseline_period", "")
    doc.setdefault("north_star", "Grow profitable sales from social media while keeping the brand safe.")
    doc.setdefault("goals", [])
    doc["guardrails"] = {k: (doc.get("guardrails") or {}).get(k, d) for k, (_, _, d) in GUARDRAILS.items()}
    doc["business"] = {k: (doc.get("business") or {}).get(k) for k in BUSINESS}
    for g in doc["goals"]:
        g["targets"] = {int(k): v for k, v in (g.get("targets") or {}).items()}
    return doc


def load(brand: Brand) -> dict:
    """The goals in force: your saved version if you've edited them, else the brand's starting values."""
    saved = get_state(brand.slug, "goals")
    if not saved:
        return _defaults(brand)
    doc = json.loads(saved)
    for g in doc["goals"]:
        g["targets"] = {int(k): v for k, v in g["targets"].items()}
    base = _defaults(brand)  # keys added in later versions still get a value
    doc["guardrails"] = {**base["guardrails"], **doc.get("guardrails", {})}
    doc["business"] = {**base["business"], **doc.get("business", {})}
    return doc


def save(brand: Brand, doc: dict) -> None:
    set_state(brand.slug, "goals", json.dumps(doc))


def guardrail(brand: Brand, key: str) -> float:
    return float(load(brand)["guardrails"][key])


def _num(text: str | None, label: str) -> float | None:
    text = (text or "").strip().replace(",", "").replace("₹", "").replace("%", "").rstrip("x")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"“{label}” must be a number (or left empty).") from None
    if value < 0:
        raise ValueError(f"“{label}” can't be negative.")
    return int(value) if value.is_integer() else value


def update_from_form(brand: Brand, form) -> dict:
    """Read the Goals page form. Raises ValueError with a plain-English message on bad input."""
    doc = load(brand)
    try:
        start = date.fromisoformat((form.get("start_date") or "").strip())
    except ValueError:
        raise ValueError("Start date must look like 2026-11-05.") from None
    doc["start_date"] = start.isoformat()
    north = (form.get("north_star") or "").strip()
    if north:
        doc["north_star"] = north[:400]
    for g in doc["goals"]:
        g["baseline"] = _num(form.get(f"{g['key']}_baseline"), f"{g['label']} — now")
        for m in MILESTONES:
            g["targets"][m] = _num(form.get(f"{g['key']}_{m}"), f"{g['label']} — day {m}")
    for key, (label, _, _) in GUARDRAILS.items():
        value = _num(form.get(f"guard_{key}"), label)
        if value is None:
            raise ValueError(f"“{label}” is a safety limit — it can't be empty.")
        doc["guardrails"][key] = value
    for key, (label, _) in BUSINESS.items():
        doc["business"][key] = _num(form.get(f"biz_{key}"), label)
    save(brand, doc)
    return doc


# ---------------------------------------------------------------- progress

def day_number(doc: dict, today: date | None = None) -> int:
    return ((today or date.today()) - date.fromisoformat(doc["start_date"])).days


def expected(goal: dict, day: int) -> float | None:
    """Where this goal should be by `day`: a straight line from today's baseline to each milestone."""
    points = [(0, goal.get("baseline"))] + [(m, goal["targets"].get(m)) for m in MILESTONES]
    points = [(d, v) for d, v in points if v is not None]
    if not points:
        return None
    if day <= points[0][0]:
        return points[0][1]
    for (d1, v1), (d2, v2) in zip(points, points[1:]):
        if day <= d2:
            return v1 + (v2 - v1) * (day - d1) / (d2 - d1)
    return points[-1][1]


def measured(brand: Brand) -> dict:
    """Latest measured values: {key: {"value", "at", "source"}} (written by the Finance/SMO agents)."""
    return json.loads(get_state(brand.slug, "metrics:latest") or "{}")


def record(brand: Brand, values: dict, source: str) -> None:
    """Store new measurements (only real numbers; None means 'couldn't measure')."""
    latest = measured(brand)
    now = datetime.now().strftime("%d %b %I:%M %p")
    for key, value in values.items():
        if value is not None:
            latest[key] = {"value": round(float(value), 2), "at": now, "source": source}
    set_state(brand.slug, "metrics:latest", json.dumps(latest))


def progress(brand: Brand, today: date | None = None) -> list[dict]:
    """Each goal with its current value, where it should be today, and a traffic light."""
    doc = load(brand)
    day = day_number(doc, today)
    now = measured(brand)
    out = []
    for g in doc["goals"]:
        target_now = expected(g, max(day, 0))
        current = (now.get(g["key"]) or {}).get("value")
        status = "nodata"
        if current is not None and target_now:
            ratio = current / target_now if g["better"] == "higher" else (target_now / current if current else 9)
            status = "green" if ratio >= 1 else "amber" if ratio >= 0.9 else "red"
        out.append({**g, "current": current, "target_now": None if target_now is None else round(target_now, 2),
                    "final": g["targets"].get(90), "status": status,
                    "measured_at": (now.get(g["key"]) or {}).get("at")})
    return out


def summary_for_agents(brand: Brand) -> str:
    """Plain text the agents read before every decision (numbers only from here and their tools)."""
    doc = load(brand)
    day = day_number(doc)
    lines = [f"NORTH STAR: {doc['north_star']}", f"Today is day {day} of the 90-day plan (started {doc['start_date']}).",
             "GOALS (now → where we should be today → day 90):"]
    for p in progress(brand):
        cur = "no data yet" if p["current"] is None else f"{p['current']}{p['unit']}"
        lines.append(f"- {p['label']}: {cur} → {p['target_now']}{p['unit']} → {p['final']}{p['unit']} "
                     f"({'better higher' if p['better'] == 'higher' else 'better lower'}; status {p['status']})")
    lines.append("GUARDRAILS (never cross): " + "; ".join(
        f"{GUARDRAILS[k][0]} {v}{GUARDRAILS[k][1]}" for k, v in doc["guardrails"].items()))
    biz = [f"{BUSINESS[k][0]}: {v}{BUSINESS[k][1]}" for k, v in doc["business"].items() if v is not None]
    lines.append("BUSINESS NUMBERS: " + ("; ".join(biz) if biz else "not given by the owner yet — say so if needed"))
    return "\n".join(lines)
