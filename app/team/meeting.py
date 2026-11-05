"""The weekly team meeting (runs at your weekly plan time, or from the Team page / chat):

1. 🔎 Researcher writes the market brief.
2. 📈 SMO and 💰 Finance write their results briefs (when Meta ads / Shopify numbers are available).
3. 🧠 Manager reads everything and sets the week's direction.
4. 🗺 Planner makes the week's plan following that direction.
5. 🔎 Researcher reviews the plan once; if needed the Planner revises it once.
6. 🗺 Planner proposes the ad campaign structure (any spend waits for your ✅).
7. You get the plan on Telegram as usual. The 🎨 Creator makes drafts each morning as before.

Each step that fails is reported and skipped — the plan still gets made, so you never lose a week.
"""

import logging

from app import claude, planner, publisher, shopify, telegram
from app.brands import load_brand
from app.team import manager, researcher

log = logging.getLogger(__name__)
STEP_ERRORS = (claude.ClaudeError, publisher.PublishError, shopify.ShopifyError, telegram.TelegramError)


def _skip(step: str, e: Exception) -> str:
    """Note a skipped step. Expected problems are explained; anything else is logged in full for fixing."""
    if isinstance(e, STEP_ERRORS):
        return f"{step} skipped ({e})"
    log.exception("Team meeting: %s failed", step)
    return f"{step} skipped (unexpected problem: {type(e).__name__} — details on the Activity page)"


def run(slug: str) -> str:
    from app import jobs
    brand = load_brand(slug)
    notes, cost = [], 0.0
    try:
        jobs.prepare_for_planning(slug)
    except Exception as e:  # noqa: BLE001 — plan with what we have
        notes.append(_skip("refreshing products and results", e))

    try:
        r = researcher.run(brand)
        cost += r["cost_usd"]
        researcher.tell_owner(brand, r)
        notes.append("market brief written")
    except Exception as e:  # noqa: BLE001 — one agent's problem never stops the meeting
        notes.append(_skip("market research", e))

    for name in ("smo", "finance"):
        try:
            module = __import__(f"app.team.{name}", fromlist=["weekly_brief"])
        except ImportError:
            continue
        try:
            b = module.weekly_brief(brand)
            if b:
                cost += b["cost_usd"]
                notes.append(f"{name} brief written")
        except Exception as e:  # noqa: BLE001
            notes.append(_skip(f"{name} brief", e))

    direction_note = ""
    try:
        d = manager.direct(brand)
        cost += d["cost_usd"]
        direction_note = d["data"]["plan_instructions"]
        manager.tell_owner(brand, d)
        notes.append("direction set")
    except Exception as e:  # noqa: BLE001
        notes.append(_skip("manager", e))

    result = planner.make_plan(brand, note=direction_note)
    cost += result["cost_usd"]
    rows = planner.upcoming_plan(brand)
    try:
        review, review_cost = researcher.review_plan(brand, rows)
        cost += review_cost
        if review["verdict"] == "revise" and review["revision_request"].strip():
            result = planner.make_plan(brand, note=(direction_note + "\n\nThe Market Researcher reviewed your first "
                                                    "version and asks: " + review["revision_request"]).strip())
            cost += result["cost_usd"]
            rows = planner.upcoming_plan(brand)
            notes.append("plan revised after the Researcher's review")
        else:
            notes.append("plan approved by the Researcher")
    except Exception as e:  # noqa: BLE001
        notes.append(_skip("plan review", e))

    try:
        from app.team import campaigns
        c = campaigns.plan(brand)
        if c:
            cost += c["cost_usd"]
            notes.append("ad campaign plan proposed")
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        notes.append(_skip("campaign plan", e))

    try:
        telegram.send_message(brand, telegram.format_plan(brand, result["summary"], rows, None))
    except telegram.TelegramError as e:
        notes.append(f"couldn't send the plan to Telegram ({e})")
    msg = f"Team meeting done: {len(result['rows'])} posts planned; " + "; ".join(notes) + \
          f". Cost about US${cost:.2f}."
    log.info(msg)
    return msg
