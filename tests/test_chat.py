"""Chat checks: talking to the agent in plain words (typed or voice), tools, and ✅/❌ confirmations."""

import json
from types import SimpleNamespace

from app import assistant, jobs, producer, telegram, voice
from app.brands import load_brand
from app.db import get_state
from test_control_center import _token, client  # noqa: F401 — shared fixture
from test_phase1 import brand  # noqa: F401 — shared fixture
from test_phase2 import _plan_row, studio  # noqa: F401 — shared fixture


def _use(name, **args):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}", name=name, input=args)


def _text(t):
    return SimpleNamespace(type="text", text=t)


class ChatClaude:
    """Plays back a script of Claude turns: each item is a list of blocks; tool turns end in tool_use."""

    def __init__(self, *turns):
        self.turns, self.calls = list(turns), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # as sent at the time
        blocks = self.turns.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return SimpleNamespace(content=blocks, stop_reason=stop, model="claude-opus-5-5",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def test_reply_uses_tools_and_remembers(studio):
    fake = ChatClaude([_use("list_products")], [_text("You have 2 styles in stock.")])
    out = assistant.reply(studio.brand, "kaunse products hain?", client=fake)
    assert out["text"] == "You have 2 styles in stock." and out["confirmations"] == []
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "₹" in result["content"]  # real Shopify prices, from the tool
    assert {t["name"] for t in fake.calls[0]["tools"]} >= {"make_reel", "approve_draft", "get_stats"}
    assert [t["role"] for t in assistant.history(studio.brand)] == ["owner", "agent"]
    # the next message carries the earlier conversation
    fake2 = ChatClaude([_text("ok")])
    assistant.reply(studio.brand, "thanks", client=fake2)
    first = fake2.calls[0]["messages"]
    assert first[0]["content"] == "kaunse products hain?" and first[1]["role"] == "assistant"


def test_approve_waits_for_confirmation(studio):
    draft_id = producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    fake = ChatClaude([_use("approve_draft", draft_id=draft_id)], [_text("Press ✅ below to approve.")])
    out = assistant.reply(studio.brand, f"approve draft {draft_id}", client=fake)
    assert producer.get_draft(studio.brand, draft_id)["status"] == "pending"  # not done yet
    (c,) = out["confirmations"]
    assert f"#{draft_id}" in c["summary"] and assistant.open_confirmations(studio.brand) == [c]
    assert "approved" in assistant.confirm(studio.brand, c["token"], True)
    assert producer.get_draft(studio.brand, draft_id)["status"] == "approved"
    assert "already been answered" in assistant.confirm(studio.brand, c["token"], True)
    assert assistant.open_confirmations(studio.brand) == []


def test_saying_no_does_nothing(studio):
    draft_id = producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    c = assistant.ask_confirmation(studio.brand, "reject_draft", {"draft_id": draft_id})
    assert "didn't" in assistant.confirm(studio.brand, c["token"], False)
    assert producer.get_draft(studio.brand, draft_id)["status"] == "pending"


def test_make_reel_and_plan_start_jobs(studio, monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: started.append((action, kw)) or True)
    msg = assistant.run_tool(studio.brand, "make_reel", {"product": "active brief", "idea": "gym morning"})
    assert "reel" in msg and started[0][0] == "reel" and started[0][1]["note"] == "gym morning"
    assistant.run_tool(studio.brand, "make_plan", {"instructions": "more reels for Diwali"})
    assert started[1] == ("plan", {"note": "more reels for Diwali"})
    assert "No product matched" in assistant.run_tool(studio.brand, "make_image", {"product": "zzz qqq"})


def test_remember_instruction_and_sale_day(studio):
    c = assistant.ask_confirmation(studio.brand, "remember_instruction", {"instruction": "Never say cheap"})
    assistant.confirm(studio.brand, c["token"], True)
    assert "- Never say cheap" in load_brand("skipperpacks").rules
    assert "Added" in assistant.run_tool(studio.brand, "add_sale_day", {"date": "2099-11-27", "name": "Black Friday"})
    days = load_brand("skipperpacks").config["festivals"]["custom_dates"]
    assert any(d["name"] == "Black Friday" for d in days)


def test_voice_reads_transcript_from_model_output(brand, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTest123456789")
    body = {"steps": [{"type": "user_input", "content": [{"type": "text", "text": "Transcribe…"}]},
                      {"type": "model_output", "content": [{"type": "text", "text": "kal ek reel banao"}]}]}
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, json=lambda: body)
    monkeypatch.setattr(voice.requests, "post", fake_post)
    assert voice.transcribe(brand, b"OggS...") == "kal ek reel banao"
    assert sent[0]["input"][1]["type"] == "audio"


def test_telegram_voice_and_text_go_to_the_assistant(brand, monkeypatch):
    msgs, buttons, heard = [], [], []
    monkeypatch.setattr(telegram, "send_message", lambda b, t: msgs.append(t))
    monkeypatch.setattr(telegram, "send_buttons", lambda b, t, bt=None: buttons.append((t, bt)))
    monkeypatch.setattr(telegram, "_call", lambda *a, **k: {})
    monkeypatch.setattr(telegram, "download_file", lambda token, file_id: b"audio")
    monkeypatch.setattr(voice, "transcribe", lambda b, data, mime="audio/ogg": "pause posting please")
    monkeypatch.setattr(assistant, "reply", lambda b, text: heard.append(text) or {
        "text": "Done <ok>", "confirmations": [{"token": "abcd", "summary": "Resume posting"}]})
    bot = telegram.Bot(lambda: brand)
    bot.handle(brand, {"chat": {"id": 12345}, "voice": {"file_id": "F1", "duration": 4}}, "12345")
    bot.handle(brand, {"chat": {"id": 12345}, "text": "aaj kya post hoga?"}, "12345")
    assert heard == ["pause posting please", "aaj kya post hoga?"]
    assert "I heard" in msgs[0] and "Done &lt;ok&gt;" in msgs[1]
    assert buttons[0][1] == [("✅ Yes, do it", "k:abcd"), ("❌ No", "n:abcd")]
    bot.handle(brand, {"chat": {"id": 12345}, "voice": {"file_id": "F2", "duration": 999}}, "12345")
    assert "under 3 minutes" in msgs[-1]


def test_telegram_yes_button_confirms(brand, monkeypatch):
    msgs = []
    monkeypatch.setattr(telegram, "send_message", lambda b, t: msgs.append(t))
    monkeypatch.setattr(telegram, "_call", lambda *a, **k: {})
    from app import publisher
    publisher.set_paused(brand, True)
    c = assistant.ask_confirmation(brand, "resume_posting", {})
    telegram.Bot(lambda: brand).handle_button(brand, {"id": "q1", "from": {"id": 12345}, "data": f"k:{c['token']}"},
                                              "12345")
    assert "resumed" in msgs[-1] and not publisher.is_paused(brand)


def test_chat_page(client, monkeypatch):
    replies = []
    monkeypatch.setattr(assistant, "reply", lambda b, text: replies.append(text) or {})
    assert "Chat with your agent" in client.get("/chat").get_data(as_text=True)
    client.post("/chat", data={"csrf": _token(client), "message": "show my queue"})
    assert replies == ["show my queue"]
    brand = load_brand("skipperpacks")
    c = assistant.ask_confirmation(brand, "resume_posting", {})
    page = client.get("/chat").get_data(as_text=True)
    assert "Resume posting" in page and f"/chat/confirm/{c['token']}/yes" in page
    r = client.post(f"/chat/confirm/{c['token']}/no", data={"csrf": _token(client)}, follow_redirects=True)
    assert "didn" in r.get_data(as_text=True) and get_state("skipperpacks", f"pending:{c['token']}") is None
    assert json.loads(json.dumps(assistant.open_confirmations(brand))) == []


def test_every_read_tool_answers(studio, monkeypatch):
    monkeypatch.setattr(jobs, "start", lambda slug, action, **kw: True)
    draft_id = producer.make_draft(studio.brand, _plan_row(), client=studio.claude)
    assert f"#{draft_id}" in assistant.run_tool(studio.brand, "get_overview", {})
    for name, args in [("get_overview", {}), ("get_plan", {}), ("list_drafts", {"status": "all"}), ("get_queue", {}),
                       ("get_stats", {"days": 7}), ("refresh_products", {}), ("make_drafts", {"days": 3}),
                       ("regenerate_draft", {"draft_id": draft_id, "instructions": "brighter"}),
                       ("edit_caption", {"draft_id": draft_id, "caption": "New caption ₹999"}), ("pause_posting", {})]:
        out = assistant.run_tool(studio.brand, name, args)
        assert out and "Unknown tool" not in out, name
    d = producer.get_draft(studio.brand, draft_id)
    assert d["caption"] == "New caption ₹999"
    for name in assistant.NEEDS_CONFIRMATION - {"resume_posting", "remember_instruction"}:
        assert assistant.describe(studio.brand, name, {"draft_id": draft_id})
