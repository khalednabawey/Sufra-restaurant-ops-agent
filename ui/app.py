"""Chainlit chat UI. It talks to the API over HTTP, the same way a real client would.

Each chat profile is one demo user (the role switcher). Write requests show Confirm / Cancel
before anything is created; approvers get Approve / Reject buttons for requests routed to them.

    cd ui && uv run chainlit run app.py --port 8501      (API on SUFRA_API_URL, default :8000)
"""

from __future__ import annotations

import json
import os

import chainlit as cl
import httpx

API = os.environ.get("SUFRA_API_URL", "http://localhost:8000")
TIMEOUT = httpx.Timeout(90.0)
APPROVERS = {"owner", "area_manager"}
TOOL_LABELS = {"request_reorder": "Reorder request", "propose_price_change": "Price change request"}


def _users() -> list[dict]:
    return httpx.get(f"{API}/auth/demo-users", timeout=10).json()


STARTERS = {
    "owner": ["ليش الهدر عالي في جدة في مايو؟", "Compare all branches on revenue for May 2026",
              "Which menu item has the lowest margin?", "What requests are waiting for my approval?"],
    "area_manager": ["قارن فروعي في المبيعات لشهر أبريل", "Which items are low on stock at Olaya?",
                     "Labour cost ratio for my branches during Ramadan",
                     "What requests are waiting for my approval?"],
    "branch_manager": ["كم كانت مبيعات فرعي في مايو؟", "Which items are low on stock?",
                       "Order 40 litres of milk"],
    "staff": ["What are my shifts on 2026-06-01?", "ما هي درجة حرارة حفظ الحليب؟"],
}


@cl.set_chat_profiles
async def chat_profiles():
    return [cl.ChatProfile(name=u["label"], markdown_description=f"**{u['role'].replace('_', ' ')}** · "
                                                                 f"branches {', '.join(u['branches'])}",
                           starters=[cl.Starter(label=q, message=q) for q in STARTERS[u["role"]]])
            for u in _users()]


async def api(method: str, path: str, **kwargs) -> dict:
    headers = {"Authorization": f"Bearer {cl.user_session.get('token')}"}
    async with httpx.AsyncClient(base_url=API, timeout=TIMEOUT) as client:
        r = await client.request(method, path, headers=headers, **kwargs)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(str(detail))
    return r.json()


def _approval_button() -> list[cl.Action]:
    if cl.user_session.get("role") in APPROVERS:
        return [cl.Action(name="show_approvals", payload={}, label="Requests waiting for me")]
    return []



@cl.on_chat_start
async def start():
    label = cl.user_session.get("chat_profile")
    user = next(u for u in _users() if u["label"] == label)
    async with httpx.AsyncClient(base_url=API, timeout=10) as client:
        session = (await client.post("/auth/demo-login", json={"user_id": user["user_id"]})).json()
    for key in ("token", "role", "branches", "user_id"):
        cl.user_session.set(key, session[key])
    cl.user_session.set("thread_id", None)
    await cl.Message(f"Signed in as **{session['label']}** · branches {', '.join(session['branches'])}.\n\n"
                     "Ask in Arabic or English, e.g. *كم كانت مبيعات الفرع في مايو؟* or "
                     "*Which items are low on stock?*", actions=_approval_button()).send()


async def show_reply(reply: dict) -> None:
    cl.user_session.set("thread_id", reply["thread_id"])
    if reply["status"] == "needs_confirmation":
        lines = []
        for c in reply["confirm"]["calls"]:
            args = ", ".join(f"{k}: {v}" for k, v in c["args"].items() if v not in (None, ""))
            lines.append(f"- **{TOOL_LABELS.get(c['name'], c['name'])}**: {args}")
        arabic = reply["confirm"].get("language") == "ar"
        title = "أؤكد إنشاء هذا الطلب؟ لن يُنفّذ شيء قبل موافقة المسؤول." if arabic else \
            "Create this request? Nothing is ordered or changed until an approver says yes."
        msg = cl.Message(f"{title}\n\n" + "\n".join(lines), actions=[
            cl.Action(name="confirm_write", payload={"confirm": True}, label="Confirm" if not arabic else "تأكيد"),
            cl.Action(name="confirm_write", payload={"confirm": False}, label="Cancel" if not arabic else "إلغاء"),
        ])
        await msg.send()
        cl.user_session.set("confirm_msg", msg)
        return
    footer = []
    if reply.get("tools_used"):
        footer.append("tools: " + ", ".join(dict.fromkeys(t["name"] for t in reply["tools_used"])))
    if reply.get("provider"):
        footer.append(f"model: {reply['provider']} · {reply.get('model')}")
    msg = cl.Message(reply["answer"] + (f"\n\n_{' · '.join(footer)}_" if footer else ""),
                     actions=_approval_button())
    await msg.send()


@cl.on_message
async def on_message(message: cl.Message):
    try:
        async with cl.Step(name="Sufra agent", type="run"):
            reply = await api("POST", "/chat", json={"message": message.content,
                                                     "thread_id": cl.user_session.get("thread_id")})
    except RuntimeError as exc:
        await cl.Message(f"⚠️ {exc}").send()
        return
    await show_reply(reply)


@cl.action_callback("confirm_write")
async def on_confirm(action: cl.Action):
    msg = cl.user_session.get("confirm_msg")
    await (msg.remove_actions() if msg else action.remove())
    try:
        reply = await api("POST", "/chat/confirm", json={"thread_id": cl.user_session.get("thread_id"),
                                                         "confirm": action.payload["confirm"]})
    except RuntimeError as exc:
        await cl.Message(f"⚠️ {exc}").send()
        return
    await show_reply(reply)


@cl.action_callback("show_approvals")
async def on_show_approvals(action: cl.Action):
    try:
        data = await api("GET", "/actions", params={"status": "pending"})
    except RuntimeError as exc:
        await cl.Message(f"⚠️ {exc}").send()
        return
    waiting = data.get("waiting_for_you", [])
    if not waiting:
        await cl.Message("Nothing is waiting for your approval.").send()
        return
    for a in waiting:
        p = a["payload"] if isinstance(a["payload"], dict) else json.loads(a["payload"])
        if a["action_type"] == "reorder":
            what = (f"Reorder #{a['action_id']} · {a['branch_id']} · {p['qty']:g} {p['unit']} {p['name_en']} "
                    f"({p['name_ar']}) from {p['supplier']} · **{a['amount']:,.2f} {a['currency']}**")
        else:
            what = (f"Price change #{a['action_id']} · {p['name_en']} ({p['name_ar']}) · {p['current_price']:g} → "
                    f"**{p['new_price']:g} {p['currency']}** ({p['change_pct']:+g}%) · reason: {p['reason']}")
        await cl.Message(f"{what}\n\nRequested by {a['requested_by']} ({a['requested_role'].replace('_', ' ')}).",
                         actions=[cl.Action(name="decide", payload={"id": a["action_id"], "d": "approved"},
                                            label="Approve"),
                                  cl.Action(name="decide", payload={"id": a["action_id"], "d": "rejected"},
                                            label="Reject")]).send()


@cl.action_callback("decide")
async def on_decide(action: cl.Action):
    card = (cl.user_session.get("decision_msgs") or {}).pop(action.payload["id"], None)
    await (card.remove_actions() if card else action.remove())
    try:
        out = await api("POST", f"/actions/{action.payload['id']}/decision", json={"decision": action.payload["d"]})
    except RuntimeError as exc:
        await cl.Message(f"⚠️ {exc}").send()
        return
    a = out["action"]
    await cl.Message(f"Request #{a['action_id']} is now **{a['status']}**.").send()
