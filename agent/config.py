"""Agent settings, read from the environment (.env in local dev). Model ids never live in code."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    primary_model: str
    fallback_model: str
    groq_api_key: str
    openrouter_api_key: str
    timeout_seconds: float
    max_retries: int
    primary_max_retries: int  # 0: on a Groq rate limit, answer from the fallback instead of waiting
    max_tool_rounds: int
    mcp_url: str | None  # streamable-http URL of the MCP server; unset = run the server in-process
    mode: str  # "router" (Groq, then OpenRouter), or "groq" / "openrouter" alone (evals per provider)


def settings() -> Settings:
    return Settings(
        primary_model=os.environ.get("LLM_PRIMARY_MODEL", ""),
        fallback_model=os.environ.get("LLM_FALLBACK_MODEL", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "20")),
        max_retries=int(os.environ.get("LLM_MAX_RETRIES", "2")),
        primary_max_retries=int(os.environ.get("LLM_PRIMARY_MAX_RETRIES", "0")),
        max_tool_rounds=int(os.environ.get("AGENT_MAX_TOOL_ROUNDS", "4")),
        mcp_url=os.environ.get("MCP_URL") or None,
        mode=os.environ.get("LLM_MODE", "router"),
    )
