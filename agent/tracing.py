"""Langfuse tracing. Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set.

One trace per chat turn: a root "agent" observation, the LangChain callback for every graph node,
model call and token count inside it, and trace attributes for filtering in the dashboard:
user, session (the thread), role and language tags, and, once the turn ends, which provider and
model answered, whether it fell back, refused, asked for approval or flagged numbers.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("sufra.tracing")


def enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


@dataclass
class Turn:
    callbacks: list = field(default_factory=list)
    trace_id: str | None = None
    _span: Any = None

    def finish(self, result: dict[str, Any], extra_tags: list[str] | None = None) -> None:
        """Tag the trace with what actually happened in the turn."""
        if self._span is None:
            return
        from langfuse import propagate_attributes
        flags = result.get("flags") or []
        tags = [f"provider:{result.get('provider') or 'none'}"]
        if result.get("provider") == "openrouter":
            tags.append("fallback")
        if "injection" in flags:
            tags.append("refused:injection")
        if any(not r.get("ok") and (r.get("error") or "").startswith("not_permitted")
               for r in result.get("turn_results", [])):
            tags.append("refused:scope")
        if result.get("__interrupt__"):
            tags.append("approval_requested")
        if "unverified_numbers" in flags:
            tags.append("unverified_numbers")
        tags += extra_tags or []
        with propagate_attributes(tags=tags, metadata={"model": str(result.get("model"))}):
            messages = result.get("messages") or []
            self._span.update(output=messages[-1].text if messages else None)


@contextmanager
def turn(name: str, *, user_id: str, session_id: str, role: str, language: str | None = None,
         question: str | None = None, tags: list[str] | None = None) -> Iterator[Turn]:
    if not enabled():
        yield Turn()
        return
    try:
        from langfuse import get_client, propagate_attributes
        from langfuse.langchain import CallbackHandler
        client = get_client()
    except Exception:  # tracing must never break a chat turn
        log.exception("Langfuse unavailable; continuing without tracing")
        yield Turn()
        return
    base_tags = [f"role:{role}"] + ([f"lang:{language}"] if language else []) + (tags or [])
    with client.start_as_current_observation(name=name, as_type="agent", input=question) as span:
        with propagate_attributes(user_id=user_id, session_id=session_id, tags=base_tags, trace_name=name):
            t = Turn(callbacks=[CallbackHandler()], trace_id=client.get_current_trace_id(), _span=span)
            yield t
