"""
OpenAI Chat Completions + Function Calling (async).
"""

from __future__ import annotations

import json
import logging
import os

from typing import cast

from openai import AsyncOpenAI, RateLimitError
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
    ChatCompletionToolUnionParam,
)

from chat.conversation import normalize_chat_turns, trim_turns
from chat.errors import QuotaExceededError
from chat.prompts import get_system_prompt
from chat.rag_hint import enrich_semantic_search_urunler_args, customer_should_force_semantic_urun
from chat.search_hint import enrich_search_products_args, customer_should_force_product_lookup
from chat.tool_definitions import DISPATCHER, json_safe, openai_tools_for_mode
from chat.tool_logging import log_tool_result

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOOL_ITERATIONS = 8

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env içinde OPENAI_API_KEY tanımlayın.")
        _client = AsyncOpenAI(api_key=key)
    return _client


async def chat(turns: str | list[dict[str, str]], mode: str = "musteri") -> str:
    client = _get_client()
    system_prompt = get_system_prompt(mode)
    tools = openai_tools_for_mode(mode)

    conv = trim_turns(normalize_chat_turns(turns))
    if not conv:
        return "(boş mesaj)"
    if conv[-1]["role"] != "user":
        return "(geçersiz sohbet sırası)"

    latest_user = conv[-1]["content"]

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(conv)

    def _musteri_mode(m: str) -> bool:
        return (m or "").strip().lower() in ("musteri", "customer", "consumer")

    forced_semantic_urun = bool(tools and _musteri_mode(mode) and customer_should_force_semantic_urun(latest_user))
    forced_product_search = bool(
        tools
        and _musteri_mode(mode)
        and customer_should_force_product_lookup(latest_user)
        and not forced_semantic_urun
    )

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            if tools:
                tc_choice: ChatCompletionToolChoiceOptionParam = "auto"
                if iteration == 0 and forced_semantic_urun:
                    tc_choice = cast(
                        ChatCompletionToolChoiceOptionParam,
                        {
                            "type": "function",
                            "function": {"name": "semantic_search_urunler"},
                        },
                    )
                elif iteration == 0 and forced_product_search:
                    tc_choice = cast(
                        ChatCompletionToolChoiceOptionParam,
                        {
                            "type": "function",
                            "function": {"name": "search_products"},
                        },
                    )
                resp = await client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=cast(list[ChatCompletionMessageParam], messages),
                    tools=cast(list[ChatCompletionToolUnionParam], tools),
                    tool_choice=tc_choice,
                    temperature=0.4,
                )
            else:
                resp = await client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=cast(list[ChatCompletionMessageParam], messages),
                    temperature=0.4,
                )
            choice = resp.choices[0]
            msg = choice.message

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                text = (msg.content or "").strip()
                out = text or "(boş yanıt)"
                tip = out if len(out) <= 400 else out[:400] + "…"
                logger.info("LLM doğrudan yanıt len=%s önizleme=%r", len(out), tip)
                return out

            assistant_entry: dict = {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_entry)

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}

                logger.info("Tool çağrısı: %s(%s)", name, args)

                if name == "search_products":
                    args = enrich_search_products_args(latest_user, args)
                if name == "semantic_search_urunler":
                    args = enrich_semantic_search_urunler_args(latest_user, args)

                fn = DISPATCHER.get(name)
                if fn is None:
                    payload = {"error": f"Bilinmeyen tool: {name}"}
                else:
                    try:
                        result = await fn(**args)
                        log_tool_result(name, result)
                        payload = {"result": json_safe(result)}
                    except TypeError as e:
                        payload = {"error": f"Geçersiz argüman: {e}"}
                    except Exception as e:
                        logger.exception("Tool hatası: %s", name)
                        payload = {"error": str(e)}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )

    except RateLimitError as e:
        raise QuotaExceededError(
            f"OpenAI API kota veya hız limiti. Model: {OPENAI_MODEL}. Detay: {e}"
        ) from e

    return "(boş yanıt)"
