"""Chat with the agent in your own words (typed or spoken).

Claude reads your message and uses the agent's "tools" — the same actions as
the buttons and slash commands — to do what you asked. Anything that changes
what goes public (approve, reject, post now, cancel, resume posting) or changes
your brand rules is NOT done straight away: you get a "✅ Yes, do it / ❌ No"
question first, so a misheard voice note can never post something by accident.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import brand_editor, claude, insights, planner, producer, publisher
from app.brands import Brand
from app.db import connect, get_state, set_state

log = logging.getLogger(__name__)
HISTORY_TURNS = 16
MAX_STEPS = 8
EXPIRY_HOURS = 2


# ---------------------------------------------------------------- the tools Claude can use

def _tool(name, description, properties=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": required or [],
                             "additionalProperties": False}}


_DRAFT = {"draft_id": {"type": "integer", "description": "The draft number, e.g. 12 for 'draft #12'."}}
_PRODUCT = {"product": {"type": "string", "description": "Product in the owner's words, e.g. 'active brief 3 pack'."},
            "idea": {"type": "string", "description": "Optional idea/scene in the owner's words."}}

TOOLS = [
    _tool("get_overview", "Current status: posting paused or not, upcoming planned posts, drafts waiting, queue, "
          "reels used this week, last product sync. Use first when the owner asks how things are going."),
    _tool("list_products", "All product styles in stock with pack size and Shopify prices."),
    _tool("get_plan", "The upcoming content plan."),
    _tool("make_plan", "Make a new plan for the coming week (replaces posts that are only planned). Takes ~1 minute "
          "and arrives on Telegram.", {"instructions": {"type": "string", "description": "Owner's wishes for the plan."}}),
    _tool("make_drafts", "Make drafts for planned posts in the next N days; they arrive on Telegram one by one.",
          {"days": {"type": "integer", "description": "How many days ahead (1–14). Default 7."}}),
    _tool("make_image", "Make one AI image post of a product (about ₹4, ~1 minute).", _PRODUCT, ["product"]),
    _tool("make_reel", "Make one AI reel of a product (about ₹70, 2–6 minutes). Weekly limit applies.", _PRODUCT,
          ["product"]),
    _tool("list_drafts", "Recent drafts with their number, status, time, product and caption start.",
          {"status": {"type": "string", "enum": ["pending", "approved", "rejected", "published", "all"]}}),
    _tool("regenerate_draft", "Make a new version of a draft, following the owner's instructions.",
          {**_DRAFT, "instructions": {"type": "string"}}, ["draft_id"]),
    _tool("edit_caption", "Replace a draft's caption with the owner's exact new caption.",
          {**_DRAFT, "caption": {"type": "string"}}, ["draft_id", "caption"]),
    _tool("approve_draft", "Approve a draft so it gets posted at its time. Owner confirms with a button.", _DRAFT,
          ["draft_id"]),
    _tool("reject_draft", "Reject a draft so it is never posted. Owner confirms with a button.", _DRAFT, ["draft_id"]),
    _tool("get_queue", "Approved posts waiting to go out, with times and status."),
    _tool("post_now", "Post an approved draft right now instead of at its time. Owner confirms with a button.", _DRAFT,
          ["draft_id"]),
    _tool("cancel_post", "Stop an approved draft from being posted. Owner confirms with a button.", _DRAFT, ["draft_id"]),
    _tool("pause_posting", "Pause all posting immediately (drafts and plans keep working)."),
    _tool("resume_posting", "Resume posting (anything overdue goes out). Owner confirms with a button."),
    _tool("get_stats", "Performance numbers and the latest lessons.",
          {"days": {"type": "integer", "enum": [7, 28]}}),
    _tool("refresh_products", "Fetch the latest products, prices and stock from Shopify."),
    _tool("add_sale_day", "Add a sale day or special date the planner should use.",
          {"date": {"type": "string", "description": "YYYY-MM-DD"}, "name": {"type": "string"},
           "note": {"type": "string"}}, ["date", "name"]),
    _tool("remember_instruction", "Save a lasting instruction into the brand rules (e.g. 'never use the word cheap', "
          "'more Hinglish'). Owner confirms with a button.",
          {"instruction": {"type": "string"}}, ["instruction"]),
]

NEEDS_CONFIRMATION = {"approve_draft", "reject_draft", "post_now", "cancel_post", "resume_posting",
                      "remember_instruction"}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)  # keeps ₹ readable for Claude


def _now(brand: Brand) -> datetime:
    return datetime.now(ZoneInfo(brand.config["schedule"].get("timezone", "Asia/Kolkata")))


def _draft_line(d: dict) -> str:
    when = d["when"].strftime("%a %d %b %I:%M %p") if d["when"] else "not scheduled"
    product = d["products"][0]["title"] if d["products"] else "?"
    return (f"#{d['id']} [{d['status']}] {when} · {d['format_label']} · {product} · "
            f"\"{(d['caption'] or '')[:90]}\"" + (f" · warnings: {'; '.join(d['warnings'])}" if d["warnings"] else ""))


def _find_product(brand: Brand, words: str) -> dict | None:
    return producer.find_style(brand, words or "")


def _start(brand: Brand, action: str, **kwargs) -> bool:
    from app import jobs
    return jobs.start(brand.slug, action, **kwargs)


def run_tool(brand: Brand, name: str, args: dict) -> str:
    """Do one tool action (non-confirmed ones) and describe the result for Claude."""
    if name == "get_overview":
        drafts = producer.recent_drafts(brand, 50)
        pending = [d for d in drafts if d["status"] == "pending"]
        plan_rows = planner.upcoming_plan(brand)
        queue = publisher.queue(brand)
        last = json.loads(get_state(brand.slug, "last_sync") or "null")
        return _json({
            "now": _now(brand).strftime("%a %d %b %Y %I:%M %p"),
            "posting_paused": publisher.is_paused(brand),
            "planned_posts_upcoming": len(plan_rows),
            "next_planned": [f"{r['when']:%a %d %b %I:%M %p} {r['format']} {r['theme']}" for r in plan_rows[:5]],
            "drafts_waiting_for_approval": [_draft_line(d) for d in pending[:10]],
            "posts_queued": [f"{g['when']:%a %d %b %I:%M %p} draft #{g['draft_id']}" for g in queue[:10]],
            "reels_this_week": f"{producer.reels_this_week(brand)} of {producer.reel_cap(brand)}",
            "products_last_synced": last["at"] if last else "never",
        })
    if name == "list_products":
        return _json([{"product": s["title"], "pack": s["pack_size"], "price": planner.price_text(
            {"min_price": s["price_from"], "max_price": s["price_to"]}), "mrp_from": s["mrp_from"],
            "colours": s["colourways"], "stock": s["stock"]} for s in planner.catalog_styles(brand)])
    if name == "get_plan":
        rows = planner.upcoming_plan(brand)
        return _json([f"{r['when']:%a %d %b %I:%M %p} · {r['format_label']} · {r['theme']} · "
                           f"{', '.join(p['title'] for p in r['products'])} · {r['idea']} · status {r['status']}"
                           for r in rows]) if rows else "No plan yet."
    if name == "make_plan":
        ok = _start(brand, "plan", note=args.get("instructions", "") or "")
        return "Started making the plan; it arrives on Telegram in about a minute." if ok else "A plan is already being made."
    if name == "make_drafts":
        days = max(1, min(14, int(args.get("days") or 7)))
        ok = _start(brand, "drafts", days=days)
        return f"Started making drafts for the next {days} days; they arrive on Telegram one by one." if ok \
            else "Drafts are already being made."
    if name in ("make_image", "make_reel"):
        style = _find_product(brand, args.get("product", ""))
        if not style:
            return "No product matched. Ask which one, using list_products."
        if name == "make_reel" and producer.reels_this_week(brand) >= producer.reel_cap(brand):
            return (f"Weekly reel limit reached ({producer.reel_cap(brand)}). The owner can raise it on "
                    "Brand → Content mix, or make an image instead.")
        ok = _start(brand, "reel" if name == "make_reel" else "image", style_id=style["id"], note=args.get("idea") or "")
        kind = "reel (2–6 minutes, about ₹70)" if name == "make_reel" else "image (about a minute, about ₹4)"
        return f"Started making a {kind} of {style['title']}; it arrives on Telegram." if ok else \
            "One is already being made — try again in a minute."
    if name == "list_drafts":
        status = args.get("status") or "pending"
        drafts = [d for d in producer.recent_drafts(brand, 40) if status == "all" or d["status"] == status]
        return "\n".join(_draft_line(d) for d in drafts[:15]) or f"No {status} drafts."
    if name == "regenerate_draft":
        d = producer.get_draft(brand, int(args["draft_id"]))
        if not d or d["status"] not in ("pending", "rejected"):
            return f"Draft #{args['draft_id']} can't be remade (it's {d['status'] if d else 'missing'})."
        note = args.get("instructions") or ""
        if not _start(brand, "regen", draft_id=d["id"], note=note):
            return "Another draft is being remade — try again in a minute."
        producer.decide(brand, d["id"], "regenerate", note or None)
        return f"Started remaking draft #{d['id']}; the new version arrives on Telegram."
    if name == "edit_caption":
        from app import factcheck, writer
        d = producer.get_draft(brand, int(args["draft_id"]))
        if not d:
            return "That draft doesn't exist."
        _, prices, packs = writer.product_facts(d["products"])
        problems = factcheck.check_text(args["caption"], brand, prices, packs)
        producer.decide(brand, d["id"], "edit_caption", args["caption"])
        return f"Caption of draft #{d['id']} updated." + (f" Warnings: {'; '.join(problems)}" if problems else "")
    if name == "get_queue":
        groups = publisher.queue(brand)
        return "\n".join(f"{g['when']:%a %d %b %I:%M %p} draft #{g['draft_id']}: " +
                         ", ".join(f"{p['platform']} {p['status']}" + (f" ({p['last_error']})" if p["last_error"] else "")
                                   for p in g["posts"]) for g in groups) or "Nothing queued."
    if name == "pause_posting":
        publisher.set_paused(brand, True)
        return "Posting paused."
    if name == "get_stats":
        rep = insights.report(brand, int(args.get("days") or 7))
        les = insights.lessons(brand)
        return insights.numbers_text(rep) + (f"\nLessons: {_json(les)}" if les else "") if rep["rows"] else \
            "No results yet for that period."
    if name == "refresh_products":
        return "Fetching products from Shopify." if _start(brand, "sync") else "Already fetching."
    if name == "add_sale_day":
        from app.web import brand_forms
        days = list((brand.config.get("festivals") or {}).get("custom_dates") or [])
        line = "\n".join(f"{d['date']} | {d['name']} | {d.get('note', '')}" for d in days)
        line += f"\n{args['date']} | {args['name']} | {args.get('note') or ''}"
        try:
            brand_editor.update_config(brand.slug, brand_forms.festivals({"custom_dates": line}))
        except Exception as e:  # noqa: BLE001 — explain to Claude, it will tell the owner
            return f"Couldn't add it: {e}"
        return f"Added {args['name']} on {args['date']}. The next plan will use it."
    return f"Unknown tool {name}."


# ---------------------------------------------------------------- confirmations

def describe(brand: Brand, name: str, args: dict) -> str:
    did = args.get("draft_id")
    d = producer.get_draft(brand, int(did)) if did else None
    what = f"draft #{did}" + (f" ({d['format_label']}, {d['products'][0]['title']})" if d and d["products"] else "")
    return {
        "approve_draft": f"Approve {what} so it gets posted at its time",
        "reject_draft": f"Reject {what} so it's never posted",
        "post_now": f"Post {what} right now on Instagram and Facebook",
        "cancel_post": f"Stop {what} from being posted",
        "resume_posting": "Resume posting (anything overdue goes out within a minute)",
        "remember_instruction": f"Add this to your brand rules for all future posts: “{args.get('instruction', '')}”",
    }[name]


def ask_confirmation(brand: Brand, name: str, args: dict) -> dict:
    token = secrets.token_hex(4)
    summary = describe(brand, name, args)
    set_state(brand.slug, f"pending:{token}", json.dumps({"tool": name, "args": args, "summary": summary,
                                                          "at": _now(brand).isoformat()}))
    return {"token": token, "summary": summary}


def open_confirmations(brand: Brand) -> list[dict]:
    """Questions still waiting for ✅/❌ (shown on the Control Center Chat page)."""
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_state WHERE brand = ? AND key LIKE 'pending:%'",
                            (brand.slug,)).fetchall()
    out = []
    for r in rows:
        item = json.loads(r["value"])
        if datetime.fromisoformat(item["at"]) >= _now(brand) - timedelta(hours=EXPIRY_HOURS):
            out.append({"token": r["key"].split(":", 1)[1], "summary": item["summary"]})
    return out


def confirm(brand: Brand, token: str, yes: bool) -> str:
    raw = get_state(brand.slug, f"pending:{token}")
    if not raw:
        return "That question has already been answered."
    with connect() as conn:  # answered once only, even if the button is pressed twice
        if not conn.execute("DELETE FROM app_state WHERE brand = ? AND key = ?", (brand.slug, f"pending:{token}")).rowcount:
            return "That question has already been answered."
    item = json.loads(raw)
    if datetime.fromisoformat(item["at"]) < _now(brand) - timedelta(hours=EXPIRY_HOURS):
        return f"That question is over {EXPIRY_HOURS} hours old — please ask again."
    if not yes:
        return "OK, I didn't do it."
    name, args = item["tool"], item["args"]
    log.info("Owner confirmed: %s %s", name, args)
    if name == "resume_posting":
        publisher.set_paused(brand, False)
        return "▶️ Posting resumed."
    if name == "remember_instruction":
        rules = brand.rules.rstrip() + "\n"
        if "## Owner's notes" not in rules:
            rules += "\n## Owner's notes (added by chat)\n"
        brand_editor.save_rules(brand.slug, rules + f"- {args['instruction'].strip()}\n")
        return "📝 Added to your brand rules (Brand → Brand rules). All new posts follow it."
    d = producer.get_draft(brand, int(args["draft_id"]))
    if not d:
        return "That draft no longer exists."
    if name == "approve_draft":
        if d["status"] != "pending":
            return f"Draft #{d['id']} is already {d['status']}."
        when = producer.decide(brand, d["id"], "approve")
        return f"✅ Draft #{d['id']} approved — it goes out {when:%a %d %b at %I:%M %p}."
    if name == "reject_draft":
        if d["status"] != "pending":
            return f"Draft #{d['id']} is already {d['status']}."
        producer.decide(brand, d["id"], "reject")
        return f"❌ Draft #{d['id']} rejected."
    with connect() as conn:
        posts = [dict(r) for r in conn.execute("SELECT * FROM posts WHERE draft_id = ? AND status IN "
                                               "('queued', 'failed', 'missed')", (d["id"],))]
    if not posts:
        return f"Draft #{d['id']} has nothing waiting to be posted (approve it first?)."
    for p in posts:
        (publisher.retry_now if name == "post_now" else publisher.cancel)(brand, p["id"])
    if name == "post_now":
        paused = " Posting is paused though — say 'resume posting' first." if publisher.is_paused(brand) else ""
        return f"📤 Draft #{d['id']} will be posted within a minute.{paused}"
    return f"Cancelled — draft #{d['id']} won't be posted."


# ---------------------------------------------------------------- the conversation

def system_prompt(brand: Brand) -> str:
    return f"""You are the social media assistant for {brand.name}, chatting with the owner on Telegram (or the
Control Center). The owner is not technical. They talk to you in their own words — English, Hindi or Hinglish,
often from a voice note, so expect small transcription mistakes and guess the obvious meaning.

How to work:
- Use the tools to look things up and to act. Never guess prices, products, drafts or numbers — check with a tool.
- If the request is clear, just do it. If it's ambiguous (which product? which draft?), ask one short question.
- Approving, rejecting, posting now, cancelling, resuming posting and saving brand rules need the owner's button
  confirmation: call the tool, then tell them to press ✅ below. Don't claim it's done.
- Mention cost when you start something that costs money (image about ₹4, reel about ₹70).
- Reply in the owner's language and style (English or Hinglish), briefly — 1–4 short lines, no headings, no jargon.
- Brand rules always apply: no suggestive content, no invented offers or prices.
- You can't change passwords or keys, or post anything that hasn't been drafted — point them to the Control Center."""


def history(brand: Brand) -> list[dict]:
    raw = get_state(brand.slug, "chat_history")
    return json.loads(raw) if raw else []


def _remember(brand: Brand, role: str, text: str) -> None:
    turns = history(brand) + [{"role": role, "text": text[:2000], "at": _now(brand).strftime("%d %b %I:%M %p")}]
    set_state(brand.slug, "chat_history", json.dumps(turns[-HISTORY_TURNS:]))


def reply(brand: Brand, text: str, client=None) -> dict:
    """Answer one owner message. Returns {"text": reply, "confirmations": [{"token", "summary"}], "cost_usd"}."""
    client = client or claude.client_for(brand)
    past = history(brand)
    messages = []
    for turn in past:  # earlier turns as plain text (append-only, no edited history)
        role = "user" if turn["role"] == "owner" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + turn["text"]
        else:
            messages.append({"role": role, "content": turn["text"]})
    if messages and messages[0]["role"] == "assistant":
        messages.insert(0, {"role": "user", "content": "(earlier conversation)"})
    now = _now(brand).strftime("%A %d %B %Y, %I:%M %p")
    current = f"[{now}] {text}"
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n" + current
    else:
        messages.append({"role": "user", "content": current})
    _remember(brand, "owner", text)

    confirmations, cost, final = [], 0.0, ""
    for _ in range(MAX_STEPS):
        response = claude.chat(client, system_prompt(brand), messages, TOOLS)
        cost += claude.cost(response.usage)
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not uses:
            final = " ".join(b.text for b in response.content if b.type == "text").strip()
            break
        results = []
        for use in uses:
            args = dict(use.input or {})
            log.info("Chat tool: %s %s", use.name, args)
            try:
                if use.name in NEEDS_CONFIRMATION:
                    c = ask_confirmation(brand, use.name, args)
                    confirmations.append(c)
                    result = f"Waiting for the owner to press ✅ to confirm: {c['summary']}. Not done yet."
                else:
                    result = run_tool(brand, use.name, args)
            except Exception as e:  # noqa: BLE001 — tell Claude what went wrong so it can explain
                log.exception("Chat tool %s failed", use.name)
                result = f"That didn't work: {e}"
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": result[:6000]})
        messages.append({"role": "user", "content": results})
    else:
        final = "Sorry, that took too many steps. Could you say it more simply?"
    final = final or ("Please confirm below." if confirmations else "Done.")
    _remember(brand, "agent", final)
    log.info("Chat reply (cost about US$%.3f)", cost)
    return {"text": final, "confirmations": confirmations, "cost_usd": cost}
