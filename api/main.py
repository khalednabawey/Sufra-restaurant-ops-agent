"""FastAPI service: sign-in for the demo users, chat with the agent, and approvals.

    uv run uvicorn api.main:app --port 8000

Every request carries the user's signed token (Authorization: Bearer ...). The token goes to the
agent's run config and on to every MCP tool call; it is never shown to the model. Threads are
named "<user_id>:<id>" and a user can only open their own.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent import tracing
from agent.config import settings
from agent.graph import build_graph
from agent.prompts import NO_MODEL, detect_language
from agent.tools import Toolbox
from mcp_server.db import database_url
from mcp_server.demo_users import DEMO_USERS
from mcp_server.scope import NotPermitted, Scope, issue_token, read_token


async def _checkpointer(stack):
    if os.environ.get("CHECKPOINTER", "postgres") == "memory":
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    pool = AsyncConnectionPool(database_url(), open=False, max_size=10,
                               kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await pool.open()
    stack.push_async_callback(pool.close)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver


@asynccontextmanager
async def lifespan(app: FastAPI):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        toolbox = await stack.enter_async_context(Toolbox(settings().mcp_url))
        app.state.toolbox = toolbox
        app.state.graph = build_graph(toolbox, checkpointer=await _checkpointer(stack))
        yield


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="Sufra Ops Agent API", version="0.2.0", lifespan=lifespan)
bearer = HTTPBearer()


class Session(BaseModel):
    token: str
    scope: Any


def session(creds: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> Session:
    try:
        return Session(token=creds.credentials, scope=read_token(creds.credentials))
    except NotPermitted as exc:
        raise HTTPException(401, str(exc)) from exc


Auth = Annotated[Session, Depends(session)]


def _user_name(scope: Scope) -> str:
    return DEMO_USERS.get(scope.user_id, (scope.user_id,))[0]


def _thread(s: Session, thread_id: str | None) -> str:
    if thread_id is None:
        return f"{s.scope.user_id}:{uuid.uuid4().hex[:12]}"
    if not thread_id.startswith(f"{s.scope.user_id}:"):
        raise HTTPException(403, "That conversation belongs to another user.")
    return thread_id


def _config(s: Session, thread_id: str, callbacks: list | None = None) -> dict:
    return {"configurable": {"thread_id": thread_id, "caller_token": s.token, "user_name": _user_name(s.scope)},
            "recursion_limit": 25, "callbacks": callbacks or []}


async def _run(s: Session, thread_id: str, payload, question: str | None, name: str) -> dict:
    """One graph run inside a Langfuse trace (a no-op when tracing is off)."""
    with tracing.turn(name, user_id=s.scope.user_id, session_id=thread_id, role=s.scope.role,
                      language=detect_language(question) if question else None, question=question) as t:
        result = await app.state.graph.ainvoke(payload, _config(s, thread_id, t.callbacks))
        t.finish(result)
    return result


async def _reply(thread_id: str, result: dict) -> dict:
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        return {"thread_id": thread_id, "status": "needs_confirmation", "confirm": interrupts[0].value}
    last = result["messages"][-1]
    return {
        "thread_id": thread_id,
        "status": "answered",
        "answer": last.text,
        "language": result.get("language"),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "tools_used": [{"name": r["name"], "args": r["args"], "ok": r["ok"], "error": r.get("error")}
                       for r in result.get("turn_results", [])],
        "pending_actions": result.get("pending_actions", []),
        "flags": result.get("flags", []),
    }


# ─── Demo sign-in ─────────────────────────────────────────────────────────

@app.get("/auth/demo-users")
def demo_users() -> list[dict]:
    return [{"user_id": uid, "label": label, "role": sc.role, "branches": list(sc.branches)}
            for uid, (label, sc) in DEMO_USERS.items()]


class LoginIn(BaseModel):
    user_id: str


@app.post("/auth/demo-login")
def demo_login(body: LoginIn) -> dict:
    if body.user_id not in DEMO_USERS:
        raise HTTPException(404, "unknown demo user")
    label, scope = DEMO_USERS[body.user_id]
    return {"token": issue_token(scope), "user_id": body.user_id, "label": label, "role": scope.role,
            "branches": list(scope.branches)}


# ─── Chat ────────────────────────────────────────────────────────────────

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None


class ConfirmIn(BaseModel):
    thread_id: str
    confirm: bool


@app.post("/chat")
async def chat(body: ChatIn, s: Auth) -> dict:
    thread_id = _thread(s, body.thread_id)
    graph = app.state.graph
    state = await graph.aget_state(_config(s, thread_id))
    if state.next:
        raise HTTPException(409, "Confirm or cancel the pending request first.")
    try:
        result = await _run(s, thread_id, {"messages": [("user", body.message)]}, body.message, "chat")
    except Exception as exc:  # last resort: never a half-answer
        raise HTTPException(503, NO_MODEL["en"]) from exc
    return await _reply(thread_id, result)


@app.post("/chat/confirm")
async def confirm(body: ConfirmIn, s: Auth) -> dict:
    thread_id = _thread(s, body.thread_id)
    graph = app.state.graph
    state = await graph.aget_state(_config(s, thread_id))
    if not state.next:
        raise HTTPException(409, "Nothing is waiting for confirmation in this conversation.")
    result = await _run(s, thread_id, Command(resume={"confirm": body.confirm}), None,
                        "confirm" if body.confirm else "cancel")
    return await _reply(thread_id, result)


@app.get("/chat/{thread_id}")
async def history(thread_id: str, s: Auth) -> dict:
    thread_id = _thread(s, thread_id)
    state = await app.state.graph.aget_state(_config(s, thread_id))
    msgs = state.values.get("messages", []) if state.values else []
    return {"thread_id": thread_id, "waiting_for_confirmation": bool(state.next),
            "messages": [{"role": m.type, "text": m.text} for m in msgs if m.type in ("human", "ai") and m.text]}


# ─── Approvals ───────────────────────────────────────────────────────────

class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = None


async def _tool(s: Session, name: str, args: dict) -> dict:
    outcome = await app.state.toolbox.call(name, args, s.token)
    if not outcome.ok:
        code = 403 if (outcome.error or "").startswith("not_permitted") else 400
        raise HTTPException(code, (outcome.error or "").split(":", 1)[-1].strip())
    return outcome.content


@app.get("/actions")
async def actions(s: Auth, status: Literal["pending", "approved", "rejected", "all"] = "pending") -> dict:
    return await _tool(s, "pending_actions_list", {"status": status})


@app.post("/actions/{action_id}/decision")
async def decide(action_id: int, body: DecisionIn, s: Auth) -> dict:
    """The only way a request is approved or rejected: an approver's explicit decision."""
    return await _tool(s, "decide_action", {"action_id": action_id, "decision": body.decision, "note": body.note})


@app.get("/health")
def health() -> dict:
    return {"ok": True}
