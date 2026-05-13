"""LangGraph stok / envanter ajanı — OpenAI tool calling + product_tools (SQL)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from agents.message_utils import last_user_text
from agents.state import AgentState
from chat.errors import QuotaExceededError
from chat.tool_definitions import json_safe
from tools import product_tools

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOOL_ITERATIONS = 6

STOCK_AGENT_SYSTEM = """Sen bir stok ve envanter asistanısın.
Satıcıya stok bilgisi veriyorsun.

KURALLAR:
- Ürün adı verilmişse o ürünün stok miktarını getir
- 'Kritik stok var mı?' sorusunda tüm kritik eşik altı ürünleri listele
- 'Hangi ürünler tükeniyor?' sorusunda stok < 10 olan ürünleri getir
- Sayısal veriyi asla tahmin etme, SQL'den çek

Türkçe, net ve kısa cevap ver."""

TOOL_CHOICE_SYSTEM = f"""{STOCK_AGENT_SYSTEM}

Şu turda kullanıcıya uzun yanıt yazma; yalnızca uygun araç çağrılarını üret.
- Kooperatifinin TÜM ürünleri stok/eşik özeti, genel envanter, "hepsi/bütün/tüm ürünler stok" → list_coop_inventory() (parametre yok; sistem kooperatif bilgisini kullanır)
- Belirli ürün adı / hangi X stoğu → get_stock_by_product(urun_adi)
- Kritik stok, eşik altı, azalan envanter genel listesi → get_low_stock_list(kooperatif_id opsiyonel)
- Tükeniyor, az kaldı, stok düşük (sayısal eşik) → get_products_stok_below(max_stok_exclusive varsayılan 10)
Birden fazla tur kullanabilirsin."""

EMPTY_REPLY = (
    "Stok verisi bulunamadı.\n"
    "Ürün adı yazarak veya ‘kritik stok’ / ‘tükeniyor’ diye tekrar sorabilirsiniz."
)

_INV_SHORT = frozenset({"bütün", "butun", "tümü", "tumu", "hepsi", "tüm", "tum", "all"})
_INV_FALLBACK_RE = re.compile(
    r"stok|envanter|kritik|eşik|esik|tüken|tuken|depoda|"
    r"ürünler.+listele|listele.+(stok|ürün)|stok.+liste|ürünlerin|urunlerin|rünler",
    re.I,
)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env içinde OPENAI_API_KEY tanımlayın.")
        _client = AsyncOpenAI(api_key=key)
    return _client


STOCK_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_coop_inventory",
            "description": (
                "Satıcının kooperatifindeki tüm ürünlerin stok, kritik eşik ve fiyat listesi "
                "(genel envanter; stok 0 dahil)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maks satır (varsayılan 200)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_by_product",
            "description": (
                "Ürün adına göre stok, kritik eşik, kategori ve kooperatif bilgisi (ILIKE)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urun_adi": {
                        "type": "string",
                        "description": "Aranacak ürün adı veya anahtar kelime",
                    },
                },
                "required": ["urun_adi"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_low_stock_list",
            "description": (
                "Kritik eşik altı veya eşit stoktaki tüm ürünleri listeler "
                "(örn. ‘kritik stok var mı?’)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kooperatif_id": {
                        "type": "integer",
                        "description": "Opsiyonel; belirli kooperatif filtresi.",
                    },
                    "limit": {"type": "integer", "description": "Maks satır (varsayılan 100)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_products_stok_below",
            "description": (
                "Stok belirtilen değerden küçük ürünler "
                "(örn. ‘tükeniyor’ için varsayılan max_stok_exclusive=10 → stok < 10)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_stok_exclusive": {
                        "type": "integer",
                        "description": "Bu değerden küçük stoklar listelenir (varsayılan 10).",
                    },
                    "limit": {"type": "integer"},
                },
            },
        },
    },
]


def _tool_round_usable(tool_results: list[dict[str, Any]]) -> bool:
    for block in tool_results:
        data = block.get("result")
        if isinstance(data, list) and len(data) > 0:
            return True
        if isinstance(data, dict) and data:
            return True
    return False


async def _dispatch_tool(
    name: str,
    raw_args: dict[str, Any],
    kooperatif_id: int | None,
) -> Any:
    args = dict(raw_args)
    if kooperatif_id is not None:
        args["kooperatif_id"] = kooperatif_id

    if name == "list_coop_inventory":
        if kooperatif_id is None:
            return []
        lim = int(raw_args.get("limit") or 200)
        return await product_tools.list_coop_inventory(kooperatif_id, limit=lim)

    if name == "get_stock_by_product":
        ad = str(args.get("urun_adi") or "").strip()
        if not ad:
            return []
        return await product_tools.get_stock_by_product(
            ad,
            kooperatif_id=kooperatif_id if kooperatif_id is not None else None,
        )
    if name == "get_low_stock_list":
        kid = args.get("kooperatif_id")
        lim = int(args.get("limit") or 100)
        return await product_tools.get_low_stock_list(
            kooperatif_id=int(kid) if kid is not None else None,
            limit=lim,
        )
    if name == "get_products_stok_below":
        mx = args.get("max_stok_exclusive")
        lim = int(args.get("limit") or 100)
        return await product_tools.get_products_stok_below(
            max_stok_exclusive=int(mx) if mx is not None else 10,
            limit=lim,
            kooperatif_id=kooperatif_id if kooperatif_id is not None else None,
        )
    raise ValueError(f"Bilinmeyen tool: {name}")


async def stock_agent_node(state: AgentState) -> dict[str, Any]:
    try:
        logger.info("stock_agent: başlıyor")
        user_text = last_user_text(state)
        if not user_text:
            return {
                "tool_results": [],
                "final_response": "Hangi ürünün stoğunu veya kritik listeyi görmek istersiniz?",
            }

        kid = state.get("kooperatif_id")
        kooperatif_id = kid if isinstance(kid, int) else None

        client = _get_client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TOOL_CHOICE_SYSTEM},
            {"role": "user", "content": user_text},
        ]
        tool_results: list[dict[str, Any]] = []

        for it in range(MAX_TOOL_ITERATIONS):
            logger.info("stock_agent: tool turu %s", it + 1)
            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                temperature=0.2,
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=cast(list[ChatCompletionToolUnionParam], STOCK_AGENT_TOOLS),
                tool_choice="auto",
            )
            msg = resp.choices[0].message
            tcalls = getattr(msg, "tool_calls", None) or []

            if not tcalls:
                direct = (msg.content or "").strip()
                if direct:
                    return {"tool_results": tool_results, "final_response": direct}
                break

            assistant_msg: dict[str, Any] = {
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
                    for tc in tcalls
                ],
            }
            messages.append(assistant_msg)

            for tc in tcalls:
                fname = tc.function.name
                try:
                    raw = tc.function.arguments or "{}"
                    args = json.loads(raw) if isinstance(raw, str) else {}
                except json.JSONDecodeError:
                    args = {}
                logger.info("stock_agent: çalıştırılıyor → %s", fname)
                try:
                    raw_out = await _dispatch_tool(fname, args, kooperatif_id)
                    safe_out = json_safe(raw_out)
                    tool_results.append({"tool": fname, "result": safe_out})
                    log_out = safe_out
                except Exception:
                    logger.exception("stock_agent: tool hatası (%s)", fname)
                    tool_results.append({"tool": fname, "result": None})
                    log_out = {"error": "tool_failed"}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"result": log_out}, ensure_ascii=False),
                    }
                )

        if kooperatif_id is not None:
            need_fb = not tool_results or not _tool_round_usable(tool_results)
            if need_fb:
                ut = (user_text or "").strip().lower()
                if ut in _INV_SHORT or _INV_FALLBACK_RE.search(user_text or ""):
                    rows_fb = await product_tools.list_coop_inventory(kooperatif_id)
                    if rows_fb:
                        logger.info(
                            "stock_agent: list_coop_inventory fallback (%s satır)",
                            len(rows_fb),
                        )
                        tool_results = [
                            {"tool": "list_coop_inventory", "result": json_safe(rows_fb)},
                        ]

        if tool_results and not _tool_round_usable(tool_results):
            return {"tool_results": tool_results, "final_response": EMPTY_REPLY}

        if not tool_results:
            return {"tool_results": [], "final_response": EMPTY_REPLY}

        payload = [{"tool": b["tool"], "result": json_safe(b["result"])} for b in tool_results]
        final_resp = await client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.35,
            messages=cast(
                list[ChatCompletionMessageParam],
                [
                    {"role": "system", "content": STOCK_AGENT_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Soru:\n{user_text}\n\n"
                            f"Araç sonuçları (JSON):\n{json.dumps(payload, ensure_ascii=False)}"
                        ),
                    },
                ],
            ),
        )
        final_text = (final_resp.choices[0].message.content or "").strip() or EMPTY_REPLY
        return {"tool_results": tool_results, "final_response": final_text}

    except RateLimitError as e:
        logger.warning("stock_agent: rate limit")
        raise QuotaExceededError(
            f"OpenAI API kota veya hız limiti. Model: {OPENAI_MODEL}. Detay: {e}"
        ) from e
    except Exception:
        logger.exception("stock_agent: beklenmeyen hata")
        return {"tool_results": [], "final_response": EMPTY_REPLY}
