"""The LangGraph agent.

    START → guard ─(injection)→ END
              └→ agent ⇄ tools      the model plans and calls MCP tools; write tools pause for the
                   └→ verify → END  user's confirmation (interrupt); verify checks every number

The signed-in user's scope and token come from the run config, never from the model or the
checkpointed state. Requests created by write tools stay pending until an approver decides
them in the app (`decide_action`, called by the API, not by the agent).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from agent import prompts
from agent.config import settings
from agent.llm import NoModelAvailable, ainvoke, chat_model
from agent.tools import WRITE_TOOLS, Toolbox
from agent.verify import unverified_numbers
from mcp_server.scope import read_token

HISTORY_MESSAGES = 30
log = logging.getLogger("sufra.agent")


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    language: str
    turn_results: list[dict[str, Any]]
    pending_actions: list[dict[str, Any]]
    tool_rounds: int
    provider: str | None
    model: str | None
    flags: list[str]


def _cfg(config: RunnableConfig) -> dict[str, Any]:
    return config.get("configurable", {})


def _history(messages: list[AnyMessage]) -> list[AnyMessage]:
    return trim_messages(messages, max_tokens=HISTORY_MESSAGES, token_counter=len, strategy="last",
                         start_on="human", include_system=False)


def build_graph(toolbox: Toolbox, checkpointer=None,
                model_factory: Callable[[list | None], Runnable] | None = None):
    cfg = settings()
    make_model = model_factory or (lambda tools: chat_model(tools=tools, cfg=cfg))

    async def guard(state: AgentState, config: RunnableConfig) -> dict:
        question = state["messages"][-1].text
        language = prompts.detect_language(question)
        update = {"question": question, "language": language, "turn_results": [], "pending_actions": [],
                  "tool_rounds": 0, "flags": [], "provider": None, "model": None}
        if prompts.looks_like_injection(question):
            update["messages"] = [AIMessage(prompts.REFUSALS[language])]
            update["flags"] = ["injection"]
        return update

    async def agent(state: AgentState, config: RunnableConfig) -> dict:
        c = _cfg(config)
        scope = read_token(c["caller_token"])
        language = state["language"]
        out_of_rounds = state.get("tool_rounds", 0) >= cfg.max_tool_rounds
        tools = None if out_of_rounds else toolbox.schemas_for_role(scope.role)
        messages = [SystemMessage(prompts.system_prompt(scope, c.get("user_name", scope.user_id), language)),
                    *_history(state["messages"])]
        if out_of_rounds:
            messages.append(SystemMessage("Tool limit reached. Answer now with the results you have, or say "
                                          "what is missing."))
        started = time.perf_counter()
        try:
            ai = await ainvoke(make_model(tools), messages)
        except NoModelAvailable:
            return {"messages": [AIMessage(prompts.NO_MODEL[language])], "flags": [*state["flags"], "no_model"]}
        log.info("agent step %.1fs provider=%s tool_calls=%s", time.perf_counter() - started,
                 ai.response_metadata.get("provider"), [t["name"] for t in ai.tool_calls])
        return {"messages": [ai], "provider": ai.response_metadata.get("provider"),
                "model": ai.response_metadata.get("model")}

    async def tools(state: AgentState, config: RunnableConfig) -> dict:
        token = _cfg(config)["caller_token"]
        calls = state["messages"][-1].tool_calls
        writes = [c for c in calls if c["name"] in WRITE_TOOLS]
        confirmed = False
        if writes:
            # Pause before anything runs. On resume this node starts again from the top, so the
            # interrupt must come before any side effect.
            decision = interrupt({"type": "confirm_write", "language": state["language"],
                                  "calls": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in writes]})
            confirmed = bool(isinstance(decision, dict) and decision.get("confirm"))

        async def run(call: dict) -> tuple[dict, ToolMessage, dict | None]:
            if call["name"] in WRITE_TOOLS and not confirmed:
                text = "cancelled: the user did not confirm, so no request was created."
                return ({"name": call["name"], "args": call["args"], "ok": False, "error": text},
                        ToolMessage(text, tool_call_id=call["id"], name=call["name"], status="error"), None)
            outcome = await toolbox.call_for_agent(call["name"], call["args"], token)
            record = {"name": outcome.name, "args": outcome.args, "ok": outcome.ok,
                      "content": outcome.content, "error": outcome.error}
            msg = ToolMessage(outcome.for_model(), tool_call_id=call["id"], name=call["name"],
                              status="success" if outcome.ok else "error")
            action = outcome.content.get("action") if outcome.ok and call["name"] in WRITE_TOOLS else None
            return record, msg, action

        done = await asyncio.gather(*(run(c) for c in calls))
        return {"messages": [m for _, m, _ in done],
                "turn_results": [*state.get("turn_results", []), *(r for r, _, _ in done)],
                "pending_actions": [*state.get("pending_actions", []), *(a for _, _, a in done if a)],
                "tool_rounds": state.get("tool_rounds", 0) + 1}

    async def verify(state: AgentState, config: RunnableConfig) -> dict:
        if {"no_model", "injection"} & set(state.get("flags", [])):
            return {}
        ai = state["messages"][-1]
        language = state["language"]
        results = [r["content"] for r in state.get("turn_results", []) if r.get("ok")]
        bad = unverified_numbers(ai.text, results, state.get("question", ""), prompts.POLICY_FACTS)
        if not bad and ai.text.strip():
            return {}
        log.info("verify: redraft needed, unverified=%s empty=%s", bad, not ai.text.strip())
        # One redraft, without tools, told exactly which figures have no source.
        c = _cfg(config)
        scope = read_token(c["caller_token"])
        fix = ("Your draft has no text. Write the answer now." if not ai.text.strip() else
               f"These numbers in your draft are not in any tool result: {', '.join(bad)}. Rewrite the answer "
               "using only numbers that appear in the tool results, or say the figure isn't available.")
        messages = [SystemMessage(prompts.system_prompt(scope, c.get("user_name", scope.user_id), language)),
                    *_history(state["messages"]), HumanMessage(fix)]
        try:
            redraft = await ainvoke(make_model(None), messages)
        except NoModelAvailable:
            redraft = None
        text = redraft.text if redraft is not None and redraft.text.strip() else ai.text
        still_bad = unverified_numbers(text, results, state.get("question", ""), prompts.POLICY_FACTS)
        if still_bad:
            text += prompts.UNVERIFIED_NOTE[language].format(nums=", ".join(still_bad))
        meta = redraft.response_metadata if redraft is not None else ai.response_metadata
        fixed = AIMessage(text, id=ai.id, response_metadata=meta)  # same id: replaces the draft
        return {"messages": [fixed], "flags": [*state.get("flags", []), "redrafted"]
                + (["unverified_numbers"] if still_bad else []),
                "provider": meta.get("provider", state.get("provider")),
                "model": meta.get("model", state.get("model"))}

    def after_guard(state: AgentState) -> str:
        return END if "injection" in state.get("flags", []) else "agent"

    def after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "verify"

    g = StateGraph(AgentState)
    g.add_node("guard", guard)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("verify", verify)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", after_guard, ["agent", END])
    g.add_conditional_edges("agent", after_agent, ["tools", "verify"])
    g.add_edge("tools", "agent")
    g.add_edge("verify", END)
    return g.compile(checkpointer=checkpointer)
