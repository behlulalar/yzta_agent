"""LangGraph ürün ajanı: tool seçimi, çalıştırma ve nihai Türkçe yanıt."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from agents.message_utils import last_user_text
from agents.state import AgentState
from chat.errors import QuotaExceededError
from chat.tool_definitions import json_safe
from tools import product_tools, rag_tools

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOOL_ITERATIONS = 6

EMPTY_TOOL_REPLY = (
    "Aradığınız kriterlere uygun ürün bulunamadı.\n"
    "Farklı bir arama yapmak ister misiniz?"
)

TOOL_CHOICE_SYSTEM = """Sen bir ürün danışmanısın. Kullanıcının mesajına göre hangi tool'u çağıracağına karar ver.

Fiyat parametreleri (search_products ve semantic_search_urunler):
- Kullanıcı 'X TL üstü' diyorsa min_fiyat=X kullan.
- Kullanıcı 'X TL altı' diyorsa max_fiyat=X kullan.
- İkisini karıştırma.

KURAL:
- Spesifik ürün adı varsa (bal, tahin, reçel vb.)
  → search_products kullan
- Niyet, amaç, özellik varsa (hediye, organik, yöresel,
  500 TL altı, anneler günü vb.)
  → semantic_search_urunler kullan
- Belirli bir ürünün detayı isteniyorsa
  → get_product_detail kullan (gerekirse önce search_products ile ürün id bul)

Bu turda kullanıcıya uzun doğal dil yanıtı verme; yalnızca uygun tool çağrılarını üret. Gerekirse birden fazla tool turunda ilerle."""

FINAL_ANSWER_SYSTEM = """Sen kadın kooperatifleri ürün danışmanısın. Aşağıdaki araç sonuçlarına göre Türkçe, sıcak ve samimi yanıt ver.

Kurallar:
- Kooperatifin bölgesini ve mümkünse kısa bir bağlam (elişi, yöresel üretim) paylaş.
- Stokta olmayan (stok 0 veya sonuçlar elenmiş) ürünü önerme; yalnızca verilen listede stokta olanları öne çıkar.
- Fiyatları net söyle; markdown tablo kullanabilirsin.
- Sonuçlar boşsa veya yalnızca hata varsa kısa nazikçe alternatif arama öner."""

FINAL_ANSWER_SYSTEM_SELLER = """Sen kooperatif satıcı paneli ürün/stok asistanısın.

Kurallar:
- Verilen TÜM ürün satırlarını kullan (stok 0 ve kritik eşik altı dahil); hiçbirini gizleme.
- Markdown tablo öner: | Ürün | Stok | Kritik eşik | Durum | — durumda kritik (stok ≤ eşik) veya normal yaz.
- Fiyat varsa göster; sayıları araç çıktısından al, uydurma.
- Liste boşsa kısaca bilgi eksikliği belirt."""

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env içinde OPENAI_API_KEY tanımlayın.")
        _client = AsyncOpenAI(api_key=key)
    return _client


PRODUCT_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Veritabanında ILIKE ile ürün arar. Net ürün/kategori kelimesi, 'var mı' soruları."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Arama metni (örn. bal, tahin)."},
                    "kategori": {"type": "string"},
                    "bolge": {"type": "string", "description": "İl/bölge (örn. Hakkari)."},
                    "min_fiyat": {
                        "type": "number",
                        "description": "Minimum fiyat filtresi, '500 TL üstü' gibi sorgular için.",
                    },
                    "max_fiyat": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_urunler",
            "description": (
                "Anlamsal ürün araması: hediye, organik, bütçe, bölge, kullanım amacı gibi niyetler."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Doğal dil arama niyeti."},
                    "kategori": {"type": "string"},
                    "bolge": {"type": "string"},
                    "min_fiyat": {
                        "type": "number",
                        "description": "Minimum fiyat filtresi, '500 TL üstü' gibi sorgular için.",
                    },
                    "max_fiyat": {"type": "number"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_detail",
            "description": "Ürün id ile tam ayrıntı + kooperatif iletişim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "urun_id": {"type": "integer", "description": "ürünler.id"},
                },
                "required": ["urun_id"],
            },
        },
    },
]


def _filter_in_stock(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict) and r.get("stok", 0) and int(r.get("stok") or 0) > 0:
            out.append(r)
    return out


def _semantic_is_error_payload(result: Any) -> bool:
    if not isinstance(result, list) or len(result) != 1:
        return False
    x = result[0]
    return isinstance(x, dict) and x.get("hata") == "semantic_search_kullanilamiyor"


def _rows_for_product_agent_usable(
    rows: list[Any],
    *,
    seller_mode: bool,
) -> list[dict[str, Any]]:
    if seller_mode:
        return [r for r in rows if isinstance(r, dict)]
    return _filter_in_stock(rows)


def _tool_round_had_usable_data(
    tool_results: list[dict[str, Any]],
    *,
    seller_mode: bool,
) -> bool:
    for block in tool_results:
        name = block.get("tool")
        data = block.get("result")
        if name == "get_product_detail":
            if data is not None and isinstance(data, dict):
                return True
        if name == "search_products" and isinstance(data, list):
            if _rows_for_product_agent_usable(data, seller_mode=seller_mode):
                return True
        if name == "semantic_search_urunler" and isinstance(data, list):
            if _semantic_is_error_payload(data):
                continue
            dict_rows = [x for x in data if isinstance(x, dict)]
            if _rows_for_product_agent_usable(dict_rows, seller_mode=seller_mode):
                return True
    return False


def _shape_for_final_llm(
    tool_results: list[dict[str, Any]],
    *,
    seller_mode: bool,
) -> list[dict[str, Any]]:
    shaped: list[dict[str, Any]] = []
    for block in tool_results:
        name = block.get("tool")
        data = block.get("result")
        if name == "search_products" and isinstance(data, list):
            shaped.append(
                {
                    "tool": name,
                    "result": json_safe(_rows_for_product_agent_usable(data, seller_mode=seller_mode)),
                }
            )
        elif name == "semantic_search_urunler" and isinstance(data, list):
            if _semantic_is_error_payload(data):
                shaped.append({"tool": name, "result": json_safe(data)})
            else:
                dict_rows = [x for x in data if isinstance(x, dict)]
                shaped.append(
                    {
                        "tool": name,
                        "result": json_safe(
                            _rows_for_product_agent_usable(dict_rows, seller_mode=seller_mode)
                        ),
                    }
                )
        else:
            shaped.append({"tool": name, "result": json_safe(data)})
    return shaped


async def _dispatch_tool(
    name: str,
    raw_args: dict[str, Any],
    kooperatif_id: int | None,
) -> Any:
    if name == "search_products":
        return await product_tools.search_products(
            query=raw_args.get("query"),
            kategori=raw_args.get("kategori"),
            bolge=raw_args.get("bolge"),
            min_fiyat=raw_args.get("min_fiyat"),
            max_fiyat=raw_args.get("max_fiyat"),
            kooperatif_id=kooperatif_id,
            limit=int(raw_args.get("limit") or 12),
        )
    if name == "semantic_search_urunler":
        q = str(raw_args.get("query") or "").strip() or "ürün önerisi"
        return await rag_tools.semantic_search_urunler(
            query=q,
            kategori=raw_args.get("kategori"),
            bolge=raw_args.get("bolge"),
            min_fiyat=raw_args.get("min_fiyat"),
            max_fiyat=raw_args.get("max_fiyat"),
            kooperatif_id=kooperatif_id,
        )
    if name == "get_product_detail":
        uid = raw_args.get("urun_id") if raw_args.get("urun_id") is not None else raw_args.get("product_id")
        if uid is None:
            return None
        return await product_tools.get_product_detail(int(uid), kooperatif_id=kooperatif_id)
    raise ValueError(f"Bilinmeyen tool: {name}")


async def product_agent_node(state: AgentState) -> dict[str, Any]:
    try:
        logger.info("product_agent: görev başlıyor")
        user_text = last_user_text(state)
        if not user_text:
            logger.info("product_agent: kullanıcı metni yok, boş yanıt")
            return {"tool_results": [], "final_response": EMPTY_TOOL_REPLY}

        kid = state.get("kooperatif_id")
        kooperatif_id = kid if isinstance(kid, int) else None

        client = _get_client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TOOL_CHOICE_SYSTEM},
            {"role": "user", "content": user_text},
        ]

        tool_results: list[dict[str, Any]] = []
        direct_text: str | None = None

        for it in range(MAX_TOOL_ITERATIONS):
            logger.info("product_agent: tool turu %s", it + 1)
            resp = await client.chat.completions.create(
                model=OPENAI_MODEL,
                temperature=0.2,
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=cast(list[ChatCompletionToolUnionParam], PRODUCT_AGENT_TOOLS),
                tool_choice="auto",
            )
            msg = resp.choices[0].message
            tcalls = getattr(msg, "tool_calls", None) or []

            if not tcalls:
                direct_text = (msg.content or "").strip() or None
                logger.info("product_agent: model araç çağırmadı (tur sonu)")
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
                logger.info("product_agent: çalıştırılıyor → %s", fname)
                try:
                    raw_out = await _dispatch_tool(fname, args, kooperatif_id)
                    safe_out = json_safe(raw_out)
                    tool_results.append({"tool": fname, "result": safe_out})
                    log_out = safe_out
                except Exception:
                    logger.exception("product_agent: tool hatası (%s)", fname)
                    tool_results.append({"tool": fname, "result": None})
                    log_out = {"error": "tool_failed"}

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"result": log_out}, ensure_ascii=False),
                    }
                )

        had_tool_calls = bool(tool_results)

        seller_mode = kooperatif_id is not None

        if had_tool_calls and not _tool_round_had_usable_data(tool_results, seller_mode=seller_mode):
            logger.info("product_agent: araç sonucu kullanılabilir veri yok")
            return {"tool_results": tool_results, "final_response": EMPTY_TOOL_REPLY}

        if not had_tool_calls and direct_text:
            logger.info("product_agent: doğrudan model yanıtı kullanılıyor")
            return {"tool_results": [], "final_response": direct_text}

        if not had_tool_calls:
            logger.info("product_agent: araç yok ve metin yok")
            return {"tool_results": [], "final_response": EMPTY_TOOL_REPLY}

        payload = _shape_for_final_llm(tool_results, seller_mode=seller_mode)
        logger.info("product_agent: nihai yanıt üretiliyor")
        final_sys = FINAL_ANSWER_SYSTEM_SELLER if seller_mode else FINAL_ANSWER_SYSTEM
        final_resp = await client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.65,
            messages=cast(
                list[ChatCompletionMessageParam],
                [
                    {"role": "system", "content": final_sys},
                    {
                        "role": "user",
                        "content": (
                            f"Kullanıcı sorusu:\n{user_text}\n\n"
                            f"Araç sonuçları (JSON):\n{json.dumps(payload, ensure_ascii=False)}"
                        ),
                    },
                ],
            ),
        )
        final_text = (final_resp.choices[0].message.content or "").strip()
        if not final_text:
            final_text = EMPTY_TOOL_REPLY
        logger.info("product_agent: nihai yanıt tamam (len=%s)", len(final_text))

        return {
            "tool_results": tool_results,
            "final_response": final_text,
        }

    except RateLimitError as e:
        logger.warning("product_agent: kota/rate limit")
        raise QuotaExceededError(
            f"OpenAI API kota veya hız limiti. Model: {OPENAI_MODEL}. Detay: {e}"
        ) from e
    except Exception:
        logger.exception("product_agent: beklenmeyen hata")
        return {"tool_results": [], "final_response": EMPTY_TOOL_REPLY}
