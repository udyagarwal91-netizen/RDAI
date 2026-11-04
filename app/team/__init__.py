"""The agent team: six specialists that write briefs to each other and to you.

🔎 Market Researcher → 🗺 Content Planner → 🎨 Content Creator, with 📈 SMO and 💰 Finance reading results,
and 🧠 Manager deciding. Each agent has a "skill playbook" (plain-English instructions) in app/team/skills/;
you can rewrite any playbook on the Team page — your version is saved in brands/<brand>/skills/ and wins.

They "talk" through written briefs stored in the database (team_briefs), not endless chat: cheaper,
and you can read exactly why each decision was made.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from app import telegram
from app.brands import Brand
from app.db import connect

log = logging.getLogger(__name__)
SKILLS_DIR = Path(__file__).parent / "skills"

AGENTS = {
    "researcher": {"name": "Market Researcher", "icon": "🔎",
                   "role": "Competitors, trends, customer words, timing — what's working in the market."},
    "planner": {"name": "Content Planner", "icon": "🗺",
                "role": "Weekly content plan, ad campaign structure, story sequences, tests."},
    "creator": {"name": "Content Creator", "icon": "🎨",
                "role": "Hooks, captions, images and reels for each funnel stage."},
    "smo": {"name": "SMO", "icon": "📈",
            "role": "Reads post and ad results; what to stop, scale or fix."},
    "finance": {"name": "Finance", "icon": "💰",
                "role": "ROAS, costs, budget pacing, break-even, the ROAS 10 roadmap."},
    "manager": {"name": "Manager", "icon": "🧠",
                "role": "Reads every brief, decides the week's direction, asks you only about money and risk."},
}


# ---------------------------------------------------------------- skill playbooks

def _override_path(brand: Brand, agent: str) -> Path:
    return brand.folder / "skills" / f"{agent}.md"


def skill(brand: Brand, agent: str) -> str:
    """The playbook this agent follows: your edited version if you made one, else the default."""
    custom = _override_path(brand, agent)
    if custom.exists():
        return custom.read_text(encoding="utf-8")
    return (SKILLS_DIR / f"{agent}.md").read_text(encoding="utf-8")


def is_custom(brand: Brand, agent: str) -> bool:
    return _override_path(brand, agent).exists()


def save_skill(brand: Brand, agent: str, text: str) -> None:
    if agent not in AGENTS:
        raise ValueError("Unknown agent.")
    text = text.replace("\r\n", "\n").strip()
    path = _override_path(brand, agent)
    if not text:  # empty = go back to the default playbook
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


# ---------------------------------------------------------------- briefs and decisions

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def post_brief(brand: Brand, agent: str, kind: str, title: str, body: str, data: dict | None = None,
               cost_usd: float = 0.0) -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO team_briefs (brand, agent, kind, title, body, data, cost_usd, created_at) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (brand.slug, agent, kind, title, body, json.dumps(data or {}, ensure_ascii=False),
                            cost_usd, _now()))
        brief_id = cur.lastrowid
    log.info("%s %s wrote a brief: %s", AGENTS[agent]["icon"], AGENTS[agent]["name"], title)
    return brief_id


def _brief(row) -> dict:
    b = dict(row)
    b["data"] = json.loads(b["data"] or "{}")
    b.update(agent_name=AGENTS[b["agent"]]["name"], icon=AGENTS[b["agent"]]["icon"])
    b["when"] = datetime.fromisoformat(b["created_at"]).strftime("%a %d %b, %I:%M %p")
    return b


def briefs(brand: Brand, limit: int = 30, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM team_briefs WHERE brand = ?" + (" AND kind = ?" if kind else "") + " ORDER BY id DESC LIMIT ?"
    args = (brand.slug, kind, limit) if kind else (brand.slug, limit)
    with connect() as conn:
        return [_brief(r) for r in conn.execute(sql, args)]


def latest(brand: Brand, kind: str, max_age_days: int = 10) -> dict | None:
    """The newest brief of a kind, if it's recent enough to still be useful."""
    found = briefs(brand, 1, kind)
    if not found:
        return None
    age = datetime.now() - datetime.fromisoformat(found[0]["created_at"])
    return found[0] if age <= timedelta(days=max_age_days) else None


def add_decision(brand: Brand, brief_id: int | None, decision: str, reason: str, expected: str,
                 check_after_days: int = 14) -> int:
    check_on = (date.today() + timedelta(days=max(3, min(60, int(check_after_days or 14))))).isoformat()
    with connect() as conn:
        return conn.execute("INSERT INTO team_decisions (brand, brief_id, decision, reason, expected, check_on, "
                            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (brand.slug, brief_id, decision, reason, expected, check_on, _now())).lastrowid


def decisions(brand: Brand, limit: int = 30, due_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM team_decisions WHERE brand = ?"
    args: list = [brand.slug]
    if due_only:
        sql += " AND status = 'open' AND check_on <= ?"
        args.append(date.today().isoformat())
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def review_decision(brand: Brand, decision_id: int, status: str, outcome: str) -> None:
    if status not in ("worked", "did_not_work", "unclear"):
        status = "unclear"
    with connect() as conn:
        conn.execute("UPDATE team_decisions SET status = ?, outcome = ? WHERE id = ? AND brand = ?",
                     (status, outcome[:1000], decision_id, brand.slug))


# ---------------------------------------------------------------- talking to the owner

def say(brand: Brand, agent: str, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
    """Send a Telegram message labelled with the agent's name. Never fails the caller."""
    a = AGENTS[agent]
    try:
        telegram.send_buttons(brand, f"{a['icon']} <b>{a['name']}</b>\n{text}", buttons)
    except telegram.TelegramError as e:
        log.warning("Couldn't send %s's message to Telegram: %s", a["name"], e)


def competitors(brand: Brand) -> list[str]:
    from app.db import get_state
    return json.loads(get_state(brand.slug, "team:competitors") or "[]")


def set_competitors(brand: Brand, text: str) -> list[str]:
    from app.db import set_state
    handles = []
    for raw in text.replace(",", "\n").split():
        h = raw.strip().lstrip("@").split("instagram.com/")[-1].split("?")[0].strip("/").lower()
        if h and h not in handles and all(c.isalnum() or c in "._" for c in h):
            handles.append(h)
    set_state(brand.slug, "team:competitors", json.dumps(handles[:15]))
    return handles[:15]
