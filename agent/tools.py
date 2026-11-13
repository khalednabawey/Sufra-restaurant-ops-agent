"""MCP tools for the agent.

`Toolbox` opens one MCP client (in-process, or over streamable HTTP when MCP_URL is set) and turns the
server's tools into tool schemas for the model. Two things never reach the model:

- `caller_token`: removed from every schema and filled in from the signed-in user's session when the
  tool runs, so the model can't read or forge it;
- `decide_action`: approvals are a human click in the app, so the assistant is never given the tool.

The model is also shown only the tools the user's role may call. The server checks again anyway.
(langchain-mcp-adapters pins mcp<2, and this project uses the MCP 2.x SDK, so the adapter is here.)
"""

from __future__ import annotations

import copy
import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import Client

from mcp_server.scope import TOOL_ROLES

NEVER_FOR_AGENT = frozenset({"decide_action"})
WRITE_TOOLS = frozenset({"request_reorder", "propose_price_change"})


@dataclass
class ToolOutcome:
    name: str
    args: dict[str, Any]
    ok: bool
    content: dict[str, Any] | None  # structured result when ok
    error: str | None  # "not_permitted: ..." / "invalid_request: ..." when not ok

    def for_model(self) -> str:
        if self.ok:
            return json.dumps(self.content, ensure_ascii=False, default=str)
        return self.error or "error: the tool failed"


def _model_schema(tool) -> dict[str, Any]:
    params = copy.deepcopy(tool.input_schema)
    params.get("properties", {}).pop("caller_token", None)
    if "required" in params:
        params["required"] = [r for r in params["required"] if r != "caller_token"]
    return {"type": "function",
            "function": {"name": tool.name, "description": tool.description or "", "parameters": params}}


class Toolbox:
    def __init__(self, target: Any = None):
        """target: an MCP server URL, or None to run the Sufra server in-process."""
        if target is None:
            from mcp_server.server import mcp as in_process_server
            target = in_process_server
        self._target = target
        self._stack = AsyncExitStack()
        self._client: Client | None = None
        self._schemas: dict[str, dict[str, Any]] = {}

    async def __aenter__(self) -> Toolbox:
        self._client = await self._stack.enter_async_context(Client(self._target))
        listed = await self._client.list_tools()
        self._schemas = {t.name: _model_schema(t) for t in listed.tools}
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    @property
    def names(self) -> set[str]:
        return set(self._schemas)

    def schemas_for_role(self, role: str) -> list[dict[str, Any]]:
        return [s for name, s in sorted(self._schemas.items())
                if name not in NEVER_FOR_AGENT and role in TOOL_ROLES.get(name, ())]

    async def call(self, name: str, args: dict[str, Any], caller_token: str) -> ToolOutcome:
        args = {k: v for k, v in (args or {}).items() if k != "caller_token"}
        if name not in self._schemas:
            return ToolOutcome(name, args, False, None, f"invalid_request: unknown tool {name!r}")
        result = await self._client.call_tool(name, {**args, "caller_token": caller_token})
        if result.is_error:
            text = " ".join(c.text for c in result.content if hasattr(c, "text")).strip()
            text = re.sub(r"^Error executing tool [\w-]+:\s*", "", text)  # keep "not_permitted: ..." first
            return ToolOutcome(name, args, False, None, text or "error: the tool failed")
        content = result.structured_content
        if content is None:
            text = " ".join(c.text for c in result.content if hasattr(c, "text"))
            try:
                content = json.loads(text)
            except ValueError:
                content = {"text": text}
        return ToolOutcome(name, args, True, content, None)

    async def call_for_agent(self, name: str, args: dict[str, Any], caller_token: str) -> ToolOutcome:
        if name in NEVER_FOR_AGENT:
            return ToolOutcome(name, args, False, None, "not_permitted: this action needs a person in the app.")
        return await self.call(name, args, caller_token)
