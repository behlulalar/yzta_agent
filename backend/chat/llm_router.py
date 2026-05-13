"""
Sistem yalnızca OpenAI kullanır (Chat Completions).

  OPENAI_API_KEY   — zorunlu
  OPENAI_MODEL     — varsayılan: gpt-4o-mini
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from dotenv import load_dotenv

load_dotenv()


def active_provider() -> str:
    return "openai"


def active_model_label() -> str:
    from chat.openai_client import OPENAI_MODEL

    return OPENAI_MODEL


def langgraph_chat_llm_info() -> dict[str, str]:
    """POST /chat LangGraph zinciri (supervisor + ürün/tedarik/finans) → OpenAI."""
    return {
        "pipeline": "langgraph_chat",
        "provider": "openai",
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    }


def standalone_chat_client_info() -> dict[str, str]:
    """
    chat/openai_client — araç çağrılı tek parça sohbet (isteğe bağlı kullanım).
    Ana /chat endpoint LangGraph kullanır.
    """
    return {
        "module": "chat.openai_client",
        "provider": "openai",
        "model": active_model_label(),
    }


def get_chat_fn() -> Callable[..., Awaitable[str]]:
    from chat.openai_client import chat as openai_chat

    return openai_chat
