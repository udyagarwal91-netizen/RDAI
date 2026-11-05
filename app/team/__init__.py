"""The agent team: six specialists that write briefs to each other and to you.

🔎 Market Researcher → 🗺 Content Planner → 🎨 Content Creator, with 📈 SMO and 💰 Finance reading results,
and 🧠 Manager deciding. Each agent has a "skill playbook" (plain-English instructions) in app/team/skills/;
you can rewrite any playbook on the Team page — your version is saved in brands/<brand>/skills/ and wins.

They "talk" through written briefs stored in the database (team_briefs), not endless chat: cheaper,
and you can read exactly why each decision was made.
"""

import json
import logging
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import wraps
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


# ---------------------------------------------------------------- the work log (what each agent is doing)

@contextmanager
def working(brand: Brand, agent: str, task: str):
    """Record one task in the agent's work log: start, finish, time taken, cost and result.
    Use as:  with working(brand, "researcher", "Market research") as job:  ...  job["cost_usd"] = 0.12"""
    started = time.monotonic()
    with connect() as conn:
        wid = conn.execute("INSERT INTO agent_work (brand, agent, task, started_at) VALUES (?, ?, ?, ?)",
                           (brand.slug, agent, task, _now())).lastrowid
    job = {"cost_usd": 0.0, "result": ""}
    status = "done"
    try:
        yield job
    except Exception as e:
        status, job["result"] = "failed", f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        with connect() as conn:
            conn.execute("UPDATE agent_work SET finished_at = ?, seconds = ?, status = ?, cost_usd = ?, result = ? "
                         "WHERE id = ?", (_now(), round(time.monotonic() - started, 1), status,
                                          float(job.get("cost_usd") or 0), str(job.get("result") or "")[:500], wid))


def _describe(out) -> tuple[str, float]:
    """A one-line result and the cost, read from whatever an agent's task returned."""
    if isinstance(out, dict):
        data = out.get("data") if isinstance(out.get("data"), dict) else {}
        text = data.get("headline") or out.get("summary") or out.get("title") or ""
        if not text and isinstance(out.get("metrics"), dict):
            roas = out["metrics"].get("blended_roas")
            text = "Numbers updated" + (f" — blended ROAS {roas}" if roas else "")
        return str(text), float(out.get("cost_usd") or 0)
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], (int, float)):
        first = out[0]
        return (str(first.get("verdict", "")) if isinstance(first, dict) else ""), float(out[1])
    if isinstance(out, list):
        return f"{len(out)} item(s)", 0.0
    if out is None:
        return "Nothing to do (no data yet)", 0.0
    return str(out)[:200], 0.0


def timed(agent: str, task: str):
    """Decorator: log every call of an agent's task in its work log (the first argument must be the brand)."""
    def wrap(fn):
        @wraps(fn)
        def run(brand, *args, **kwargs):
            with working(brand, agent, task) as job:
                out = fn(brand, *args, **kwargs)
                job["result"], job["cost_usd"] = _describe(out)
                return out
        return run
    return wrap


def work_log(brand: Brand, agent: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM agent_work WHERE brand = ?" + (" AND agent = ?" if agent else "") + " ORDER BY id DESC LIMIT ?"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, (brand.slug, agent, limit) if agent else (brand.slug, limit))]
    for r in rows:
        r["when"] = datetime.fromisoformat(r["started_at"]).strftime("%a %d %b, %I:%M %p")
        if r["status"] == "working" and datetime.fromisoformat(r["started_at"]) < datetime.now() - timedelta(hours=2):
            r["status"] = "interrupted"  # the app was closed while it worked
        r["duration"] = _duration(r["seconds"])
    return rows


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds // 60:.0f}m {seconds % 60:.0f}s"
    return f"{seconds // 3600:.0f}h {(seconds % 3600) // 60:.0f}m"


def work_stats(brand: Brand, agent: str, days: int = 7) -> dict:
    """Totals for one agent over the last `days` days, with a breakdown by kind of task."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT task, status, seconds, cost_usd FROM agent_work WHERE brand = ? "
                                              "AND agent = ? AND started_at >= ?", (brand.slug, agent, since))]
    by_task: dict[str, dict] = {}
    for r in rows:
        t = by_task.setdefault(r["task"], {"task": r["task"], "count": 0, "seconds": 0.0, "cost_usd": 0.0, "failed": 0})
        t["count"] += 1
        t["seconds"] += r["seconds"] or 0
        t["cost_usd"] += r["cost_usd"] or 0
        t["failed"] += r["status"] == "failed"
    total = sum(t["seconds"] for t in by_task.values())
    count = sum(t["count"] for t in by_task.values())
    for t in by_task.values():  # share of time (or of tasks, when everything took under a second)
        t["share"] = round(100 * t["seconds"] / total) if total >= 1 else round(100 * t["count"] / count) if count else 0
        t["duration"] = _duration(t["seconds"])
    return {"days": days, "tasks": len(rows), "seconds": total, "duration": _duration(total),
            "cost_usd": round(sum(t["cost_usd"] for t in by_task.values()), 3),
            "failed": sum(t["failed"] for t in by_task.values()),
            "by_task": sorted(by_task.values(), key=lambda t: t["seconds"], reverse=True)}


def now_working(brand: Brand) -> dict[str, dict]:
    """What each agent is busy with right now (only tasks started in the last 2 hours and not finished)."""
    since = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM agent_work WHERE brand = ? AND status = 'working' "
                                              "AND started_at >= ? ORDER BY id", (brand.slug, since))]
    out = {}
    for r in rows:
        r["minutes"] = max(0, int((datetime.now() - datetime.fromisoformat(r["started_at"])).total_seconds() // 60))
        out[r["agent"]] = r
    return out


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
