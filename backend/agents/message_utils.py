"""LangGraph state içinden son kullanıcı mesajını çıkarır."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from agents.state import AgentState


def last_user_text(state: AgentState | Mapping[str, Any]) -> str:
    messages: list[Any] = state.get("messages") or []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            c = m.content
            if isinstance(c, str):
                s = c.strip()
                if s:
                    return s
            continue
        if isinstance(m, BaseMessage) and getattr(m, "type", None) == "human":
            c = getattr(m, "content", "") or ""
            if isinstance(c, str) and c.strip():
                return c.strip()
    return ""
