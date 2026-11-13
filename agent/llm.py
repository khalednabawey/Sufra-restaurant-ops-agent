"""The model router: Groq first, OpenRouter when Groq fails.

Every node that calls a model goes through `chat_model()`, so fallback behaviour is the same
everywhere. The fallback fires on any provider error (rate limit, timeout, server error, bad
tool-call output). Groq gets a short timeout and no retries by default: on a rate limit Groq asks
the client to wait 10–30 s, and answering from the fallback is faster than waiting.
Each reply is tagged with the provider and model that actually answered, in
`response_metadata["provider"]` and `["model"]`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from agent.config import Settings, settings

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class NoModelAvailable(RuntimeError):
    """Both providers failed. The API turns this into a clear 'try again' message."""


def _tag(provider: str, model: str) -> RunnableLambda:
    def tag(msg: AIMessage) -> AIMessage:
        msg.response_metadata = {**msg.response_metadata, "provider": provider, "model": model}
        return msg
    return RunnableLambda(tag, name=f"tag_{provider}")


def primary(cfg: Settings) -> BaseChatModel:
    # Alone (LLM_MODE=groq, for evals), Groq waits out its rate limits instead of failing over.
    retries = max(cfg.primary_max_retries, 4) if cfg.mode == "groq" else cfg.primary_max_retries
    return ChatGroq(model=cfg.primary_model, api_key=cfg.groq_api_key, temperature=0,
                    timeout=cfg.timeout_seconds, max_retries=retries)


def fallback(cfg: Settings) -> BaseChatModel:
    return ChatOpenAI(model=cfg.fallback_model, api_key=cfg.openrouter_api_key, base_url=OPENROUTER_BASE_URL,
                      temperature=0, timeout=cfg.timeout_seconds * 2, max_retries=cfg.max_retries,
                      default_headers={"X-Title": "Sufra Ops Agent"})


def chat_model(tools: Sequence[Any] | None = None, cfg: Settings | None = None,
               primary_model: BaseChatModel | None = None,
               fallback_model: BaseChatModel | None = None) -> Runnable:
    """A runnable that answers with Groq, or OpenRouter if Groq raises. Tools are bound to both
    with the same schemas. The model arguments exist so tests can swap in fakes."""
    cfg = cfg or settings()
    first = primary_model or primary(cfg)
    second = fallback_model or fallback(cfg)
    if tools:
        first, second = first.bind_tools(tools), second.bind_tools(tools)
    groq = first | _tag("groq", cfg.primary_model)
    openrouter = second | _tag("openrouter", cfg.fallback_model)
    if cfg.mode == "groq":
        return groq
    if cfg.mode == "openrouter":
        return openrouter
    return groq.with_fallbacks([openrouter])


async def ainvoke(model: Runnable, messages: list, **kwargs: Any) -> AIMessage:
    try:
        return await model.ainvoke(messages, **kwargs)
    except Exception as exc:  # both providers failed
        raise NoModelAvailable("Both model providers are unavailable right now.") from exc
