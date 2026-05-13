"""LangGraph sipariş ajanı: sipariş oluşturma (deterministik) + takip/sorgu (OpenAI tools).

Sipariş oluşturma tarafında sistem davranışı şu makinededir:
ürünü bul (``get_product_for_order``) → miktarı sor → teslimat adresini sor → ``create_order``.
Özel durumlarda mesajlar: ürün bulunamadı, stoktan fazla miktar, boş adres (yeniden sorulur).
Ödeme URL'si yanıtta Markdown bağlantısı olarak döner.
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from typing import Any, cast

from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam

from agents.message_utils import last_user_text
from agents.state import AgentState
from chat.errors import QuotaExceededError
from chat.tool_definitions import json_safe
from tools import order_tools

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOOL_ITERATIONS = 6

MOCK_MUSTERI_AD = "Misafir Müşteri"

EMPTY_REPLY = (
    "Sipariş bilgisini bulamadım.\n"
    "Sipariş numaranızı (örn. 12) veya müşteri e‑posta/ad ile tekrar dener misiniz?"
)

TRACKING_TOOL_CHOICE_SYSTEM = """Sen sipariş takibi ve kargo asistanısın. Kullanıcı mesajına göre uygun araçları çağır.

KURALLAR:
- Açık veya çıkarılabilir sipariş numarası varsa → önce get_order(order_id), gerekirse get_order_items ve get_shipping_info ile tamamla.
- Sipariş no “12”, “#12”, “sipariş 12” → order_id=12 (tam sayı).
- Email veya müşteri adı ile arama → search_orders_by_customer kullan; sonra ilgili sipariş için get_order / kalem / kargo.
- Bu turda kullanıcıya uzun metin yazma; yalnızca tool çağrıları."""

TRACKING_FINAL_ANSWER_SYSTEM = """Sen kadın kooperatifleri sipariş danışmanısın. Araç sonuçlarına göre Türkçe, net ve nazik yanıt ver.

Kurallar:
- Sipariş durumu, tutar, müşteri özeti, kalem özeti ve kargo firması/takip bilgisini verildiği kadarıyla aktar.
- Boş veya bulunamadı ise kısaca sipariş numarası veya e‑posta ile tekrar denemesini öner.
- Markdown kullanabilirsin."""

_client: AsyncOpenAI | None = None

RE_CANCEL = re.compile(r"^\s*(iptal|vazgeç)\s*[!.]?\s*$", re.I)
RE_CREATION = re.compile(
    r"almak\s+istiyorum|satın\s+al(?:mak)?(?:\s+istiyorum)?|"
    r"sipariş\s+ver(?:mek)?(?:\s+istiyorum)?|sipariş\s+etmek|sipariş\s+oluştur",
    re.I,
)
RE_TRACK = re.compile(
    r"\bsiparişim\b|\bkargo\b|\bteslimat\b|\btakip\b|sipariş\s+durumu|siparişim\s+nerede|numaralı\s+sipariş",
    re.I,
)
RE_ORDER_NUM = re.compile(
    r"(?:sipariş|order)[^\d]{0,16}(\d{1,8})\b|#\s*(\d{1,8})\b|\b(\d{1,8})\s*(?:numaralı|nolu)?\s*sipariş\b",
    re.I,
)

_BUY_SUFFIXES = sorted(
    (
        "satın almak istiyorum",
        "sipariş vermek istiyorum",
        "sipariş etmek istiyorum",
        "almak istiyorum",
        "sipariş oluşturmak istiyorum",
        "sipariş vermek istiyorum.",
        "satın almak istiyorum.",
        "almak istiyorum.",
        "sipariş ver",
        "satın al",
    ),
    key=len,
    reverse=True,
)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env içinde OPENAI_API_KEY tanımlayın.")
        _client = AsyncOpenAI(api_key=key)
    return _client


TRACKING_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Sipariş numarasıyla özet, müşteri ve durum.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "siparisler.id"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_items",
            "description": "Sipariş kalemleri (ürün adı, miktar, fiyat).",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shipping_info",
            "description": "Kargo firması, takip numarası ve kargo durumu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_orders_by_customer",
            "description": "Müşteri e‑posta veya ada göre sipariş arama.",
            "parameters": {
                "type": "object",
                "properties": {
                    "musteri_email": {"type": "string"},
                    "musteri_ad": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
]

ORDER_CREATION_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_product_for_order",
            "description": (
                "Yeni sipariş için ürün ara (stokta olan tek satır). "
                "Yalnızca kullanıcı ürün adını net verdiyse veya sipariş oluşturma niyeti belliyse."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urun_adi": {"type": "string"},
                },
                "required": ["urun_adi"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": (
                "Adres ve miktar doğrulandıktan sonra sipariş oluşturur; demo ödeme URL döner."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urun_id": {"type": "integer"},
                    "miktar": {"type": "integer"},
                    "musteri_adi": {"type": "string"},
                    "adres": {"type": "string"},
                },
                "required": ["urun_id", "miktar", "musteri_adi", "adres"],
            },
        },
    },
]

# Geriye dönük / dokümantasyon: takip + sipariş oluşturma şemaları (LLM’de tam liste gerekirse)
ORDER_AGENT_TOOLS: list[dict[str, Any]] = TRACKING_TOOLS + ORDER_CREATION_TOOL_SCHEMAS


def _looks_like_tracking(text: str) -> bool:
    if "@" in text:
        return True
    if RE_TRACK.search(text):
        return True
    if RE_ORDER_NUM.search(text):
        return True
    return False


def _looks_like_creation(text: str) -> bool:
    return bool(RE_CREATION.search(text))


def _strip_buy_suffixes(text: str) -> str:
    t = text.strip()
    low = t.lower()
    for suf in _BUY_SUFFIXES:
        if low.endswith(suf):
            t = t[: len(t) - len(suf)].strip()
            low = t.lower()
    return t.strip(" ,.!?-")


def _parse_qty_then_product(text: str) -> tuple[int, str] | None:
    m = re.match(r"^(\d+)\s*(?:adet)?\s+(.+)$", text.strip(), re.I)
    if not m:
        return None
    qty = int(m.group(1))
    rest = m.group(2).strip()
    rest = _strip_buy_suffixes(rest)
    if qty < 1 or len(rest) < 1:
        return None
    return qty, rest


def _parse_quantity_only(text: str) -> int | None:
    m = re.match(r"^(\d+)\s*(?:adet)?\s*$", text.strip(), re.I)
    if not m:
        return None
    q = int(m.group(1))
    return q if q >= 1 else None


def _draft_from_product_row(row: dict[str, Any]) -> dict[str, Any]:
    price = row.get("fiyat")
    if isinstance(price, Decimal):
        bf = float(price)
    else:
        bf = float(price or 0)
    return {
        "urun_id": int(row["id"]),
        "ad": str(row.get("ad") or ""),
        "stok": int(row.get("stok") or 0),
        "birim": str(row.get("birim") or ""),
        "birim_fiyat": bf,
    }


def _tool_round_usable(tool_results: list[dict[str, Any]]) -> bool:
    for block in tool_results:
        data = block.get("result")
        name = block.get("tool")
        if name == "get_order" and isinstance(data, dict) and data:
            return True
        if name == "get_order_items" and isinstance(data, list) and len(data) > 0:
            return True
        if name == "get_shipping_info" and isinstance(data, dict) and data:
            return True
        if name == "search_orders_by_customer" and isinstance(data, list) and len(data) > 0:
            return True
    return False


async def _dispatch_tool(
    name: str,
    raw_args: dict[str, Any],
    kooperatif_id: int | None,
) -> Any:
    if name == "get_order":
        oid = raw_args.get("order_id")
        if oid is None:
            return None
        return await order_tools.get_order(int(oid), kooperatif_id=kooperatif_id)
    if name == "get_order_items":
        oid = raw_args.get("order_id")
        if oid is None:
            return []
        return await order_tools.get_order_items(int(oid), kooperatif_id=kooperatif_id)
    if name == "get_shipping_info":
        oid = raw_args.get("order_id")
        if oid is None:
            return None
        return await order_tools.get_shipping_info(int(oid), kooperatif_id=kooperatif_id)
    if name == "search_orders_by_customer":
        return await order_tools.search_orders_by_customer(
            musteri_email=raw_args.get("musteri_email"),
            musteri_ad=raw_args.get("musteri_ad"),
            limit=int(raw_args.get("limit") or 10),
            kooperatif_id=kooperatif_id,
        )
    if name == "get_product_for_order":
        q = (raw_args.get("urun_adi") or "").strip()
        return await order_tools.get_product_for_order(q, kooperatif_id=kooperatif_id)
    if name == "create_order":
        return await order_tools.create_order(
            urun_id=int(raw_args["urun_id"]),
            miktar=int(raw_args["miktar"]),
            musteri_adi=str(raw_args.get("musteri_adi") or MOCK_MUSTERI_AD),
            adres=str(raw_args.get("adres") or ""),
            kooperatif_id=kooperatif_id,
        )
    raise ValueError(f"Bilinmeyen tool: {name}")


async def _run_tracking_agent(state: AgentState, user_text: str) -> dict[str, Any]:
    logger.info("order_agent: takip modu (OpenAI tool döngüsü) başlıyor")
    kid = state.get("kooperatif_id")
    kooperatif_id = kid if isinstance(kid, int) else None
    client = _get_client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": TRACKING_TOOL_CHOICE_SYSTEM},
        {"role": "user", "content": user_text},
    ]
    tool_results: list[dict[str, Any]] = []

    for it in range(MAX_TOOL_ITERATIONS):
        logger.info("order_agent: takip tool turu %s", it + 1)
        resp = await client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.2,
            messages=cast(list[ChatCompletionMessageParam], messages),
            tools=cast(list[ChatCompletionToolUnionParam], TRACKING_TOOLS),
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        tcalls = getattr(msg, "tool_calls", None) or []

        if not tcalls:
            direct = (msg.content or "").strip()
            if direct:
                return {
                    "tool_results": tool_results,
                    "final_response": direct,
                    "order_step": "idle",
                    "order_draft": {},
                }
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
            logger.info("order_agent: takip aracı çalışıyor → %s", fname)
            try:
                raw_out = await _dispatch_tool(fname, args, kooperatif_id)
                safe_out = json_safe(raw_out)
                tool_results.append({"tool": fname, "result": safe_out})
                log_out = safe_out
            except Exception:
                logger.exception("order_agent: takip tool hatası (%s)", fname)
                tool_results.append({"tool": fname, "result": None})
                log_out = {"error": "tool_failed"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"result": log_out}, ensure_ascii=False),
                }
            )

    if tool_results and not _tool_round_usable(tool_results):
        return {
            "tool_results": tool_results,
            "final_response": EMPTY_REPLY,
            "order_step": "idle",
            "order_draft": {},
        }

    if not tool_results:
        return {
            "tool_results": [],
            "final_response": EMPTY_REPLY,
            "order_step": "idle",
            "order_draft": {},
        }

    payload = [{"tool": b["tool"], "result": json_safe(b["result"])} for b in tool_results]
    logger.info("order_agent: takip için nihai yanıt üretiliyor")
    final_resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.5,
        messages=cast(
            list[ChatCompletionMessageParam],
            [
                {"role": "system", "content": TRACKING_FINAL_ANSWER_SYSTEM},
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
    final_text = (final_resp.choices[0].message.content or "").strip() or EMPTY_REPLY
    return {
        "tool_results": tool_results,
        "final_response": final_text,
        "order_step": "idle",
        "order_draft": {},
    }


async def _idle_start_creation(
    user_text: str,
    kooperatif_id: int | None,
) -> dict[str, Any]:
    """Ürün araması + miktar/adres adımlarına geçiş (sipariş oluşturma)."""
    raw = user_text.strip()
    pair = _parse_qty_then_product(raw)
    if pair:
        qty, pname = pair
        logger.info(
            "order_agent: tek mesajda miktar+ürün algılandı (miktar=%s ürün=%r)",
            qty,
            pname,
        )
        row = await order_tools.get_product_for_order(pname, kooperatif_id=kooperatif_id)
        if isinstance(row, dict) and row.get("error"):
            msg = "Bu ürünü şu an bulamadım, ürün adını kontrol eder misiniz?"
            logger.info("order_agent: ürün bulunamadı (tek mesaj): %s", row.get("error"))
            return {
                "tool_results": [],
                "final_response": msg,
                "order_step": "idle",
                "order_draft": {},
            }
        draft = _draft_from_product_row(row)
        stok = draft["stok"]
        if qty > stok:
            logger.info(
                "order_agent: talep edilen miktar stoktan fazla (istenen=%s stok=%s)",
                qty,
                stok,
            )
            return {
                "tool_results": [],
                "final_response": f"Üzgünüm, stokta sadece {stok} adet var.",
                "order_step": "idle",
                "order_draft": {},
            }
        draft["miktar"] = qty
        return {
            "tool_results": [],
            "final_response": "Teslimat adresinizi paylaşır mısınız?",
            "order_step": "awaiting_address",
            "order_draft": draft,
        }

    query = _strip_buy_suffixes(raw)
    if len(query) < 2:
        logger.info("order_agent: sipariş niyeti var ama ürün adı eksik → awaiting_product")
        return {
            "tool_results": [],
            "final_response": "Hangi ürünü sipariş etmek istediğinizi yazar mısınız?",
            "order_step": "awaiting_product",
            "order_draft": {},
        }

    row = await order_tools.get_product_for_order(query, kooperatif_id=kooperatif_id)
    if isinstance(row, dict) and row.get("error"):
        logger.info("order_agent: ürün bulunamadı veya stokta yok: %s", row.get("error"))
        return {
            "tool_results": [],
            "final_response": "Bu ürünü şu an bulamadım, ürün adını kontrol eder misiniz?",
            "order_step": "idle",
            "order_draft": {},
        }

    draft = _draft_from_product_row(row)
    ad = draft["ad"]
    stok = draft["stok"]
    logger.info(
        "order_agent: ürün seçildi (urun_id=%s ad=%r stok=%s) → awaiting_quantity",
        draft["urun_id"],
        ad,
        stok,
    )
    reply = (
        f"{ad} ürününden kaç adet almak istersiniz?\n"
        f"Stokta {stok} adet mevcut."
    )
    return {
        "tool_results": [],
        "final_response": reply,
        "order_step": "awaiting_quantity",
        "order_draft": draft,
    }


async def _creation_turn(
    step: str,
    draft: dict[str, Any],
    user_text: str,
    kooperatif_id: int | None,
) -> dict[str, Any]:
    if RE_CANCEL.match(user_text):
        logger.info("order_agent: sipariş oluşturma kullanıcı tarafından iptal edildi")
        return {
            "tool_results": [],
            "final_response": "Sipariş oluşturma iptal edildi. İsterseniz yeni bir sipariş için ürün adını yazabilirsiniz.",
            "order_step": "idle",
            "order_draft": {},
        }

    if step == "awaiting_product":
        q = user_text.strip()
        if len(q) < 2:
            return {
                "tool_results": [],
                "final_response": "Ürün adını net yazabilir misiniz?",
                "order_step": "awaiting_product",
                "order_draft": {},
            }
        return await _idle_start_creation(q, kooperatif_id)

    if step == "awaiting_quantity":
        qty = _parse_quantity_only(user_text)
        if qty is None:
            return {
                "tool_results": [],
                "final_response": "Lütfen kaç adet istediğinizi rakamla yazın (örn. 2 veya 2 adet).",
                "order_step": "awaiting_quantity",
                "order_draft": draft,
            }
        stok = int(draft.get("stok") or 0)
        if qty > stok:
            logger.info(
                "order_agent: miktar stoktan fazla (istenen=%s stok=%s)",
                qty,
                stok,
            )
            return {
                "tool_results": [],
                "final_response": f"Üzgünüm, stokta sadece {stok} adet var.",
                "order_step": "awaiting_quantity",
                "order_draft": draft,
            }
        draft = {**draft, "miktar": qty}
        logger.info("order_agent: miktar alındı (%s) → awaiting_address", qty)
        return {
            "tool_results": [],
            "final_response": "Teslimat adresinizi paylaşır mısınız?",
            "order_step": "awaiting_address",
            "order_draft": draft,
        }

    if step == "awaiting_address":
        adres = user_text.strip()
        if not adres:
            return {
                "tool_results": [],
                "final_response": "Teslimat adresinizi paylaşır mısınız?",
                "order_step": "awaiting_address",
                "order_draft": draft,
            }
        uid = draft.get("urun_id")
        mid = draft.get("miktar")
        if uid is None or mid is None:
            logger.warning("order_agent: taslak eksik, akış sıfırlanıyor")
            return {
                "tool_results": [],
                "final_response": "Oturum süresi doldu veya eksik bilgi var. Hangi ürünü sipariş etmek istediğinizi tekrar yazar mısınız?",
                "order_step": "idle",
                "order_draft": {},
            }
        result = await order_tools.create_order(
            urun_id=int(uid),
            miktar=int(mid),
            musteri_adi=MOCK_MUSTERI_AD,
            adres=adres,
            kooperatif_id=kooperatif_id,
        )
        if isinstance(result, dict) and result.get("error"):
            err = str(result["error"])
            logger.info("order_agent: create_order başarısız: %s", err)
            if "stokta sadece" in err.lower():
                new_draft = {**draft}
                sk = result.get("stok_kalan")
                if sk is not None:
                    try:
                        new_draft["stok"] = int(sk)
                    except (TypeError, ValueError):
                        pass
                new_draft.pop("miktar", None)
                return {
                    "tool_results": [],
                    "final_response": f"{err}\n\nLütfen stok dahilinde bir miktar yazın.",
                    "order_step": "awaiting_quantity",
                    "order_draft": new_draft,
                }
            if "adres" in err.lower():
                return {
                    "tool_results": [],
                    "final_response": "Teslimat adresinizi paylaşır mısınız?",
                    "order_step": "awaiting_address",
                    "order_draft": draft,
                }
            return {
                "tool_results": [],
                "final_response": err,
                "order_step": "idle",
                "order_draft": {},
            }

        siparis_id = result["siparis_id"]
        tutar = float(result["toplam_tutar"])
        url = result["mock_odeme_url"]
        logger.info(
            "order_agent: sipariş tamamlandı siparis_id=%s tutar=%s TL",
            siparis_id,
            tutar,
        )
        reply = (
            "Siparişiniz başarıyla oluşturuldu!\n"
            f"Sipariş No: {siparis_id}\n"
            f"Toplam Tutar: {tutar:.2f} TL\n\n"
            "Ödeme için aşağıdaki butona tıklayabilirsiniz:\n"
            f"[ÖDEME YAP]({url})"
        )
        return {
            "tool_results": [{"tool": "create_order", "result": json_safe(result)}],
            "final_response": reply,
            "order_step": "idle",
            "order_draft": {},
        }

    return {
        "tool_results": [],
        "final_response": EMPTY_REPLY,
        "order_step": "idle",
        "order_draft": {},
    }


async def order_agent_node(state: AgentState) -> dict[str, Any]:
    try:
        logger.info("order_agent: düğüm başladı")
        kid = state.get("kooperatif_id")
        kooperatif_id = kid if isinstance(kid, int) else None
        user_text = last_user_text(state)
        if not user_text:
            return {
                "tool_results": [],
                "final_response": (
                    "Sipariş veya kargo için sipariş numaranızı veya e‑posta adresinizi yazabilirsiniz."
                ),
                "order_step": state.get("order_step") or "idle",
                "order_draft": dict(state.get("order_draft") or {}),
            }

        step_raw = (state.get("order_step") or "idle").strip().lower()
        draft_in = dict(state.get("order_draft") or {})

        if step_raw not in ("idle", "awaiting_product", "awaiting_quantity", "awaiting_address"):
            step_raw = "idle"

        # Sipariş oluşturma çok adımlı akış
        if step_raw != "idle":
            logger.info(
                "order_agent: sipariş oluşturma adımı sürdürülüyor (%s)",
                step_raw,
            )
            return await _creation_turn(step_raw, draft_in, user_text, kooperatif_id)

        if RE_CANCEL.match(user_text.strip()):
            logger.info("order_agent: kullanıcı iptal mesajı gönderdi (boşta)")
            return {
                "tool_results": [],
                "final_response": (
                    "Tamamdır. Sipariş vermek veya siparişinizi sorgulamak için istediğiniz zaman yazabilirsiniz."
                ),
                "order_step": "idle",
                "order_draft": {},
            }

        track = _looks_like_tracking(user_text)
        create = _looks_like_creation(user_text)

        if track and not create:
            return await _run_tracking_agent(state, user_text)

        if create:
            return await _idle_start_creation(user_text, kooperatif_id)

        # Belirsiz: önce takip (sipariş no / kargo anahtar kelimesi yoksa LLM yine yardımcı olur)
        return await _run_tracking_agent(state, user_text)

    except RateLimitError as e:
        logger.warning("order_agent: OpenAI hız limiti")
        raise QuotaExceededError(
            f"OpenAI API kota veya hız limiti. Model: {OPENAI_MODEL}. Detay: {e}"
        ) from e
    except Exception:
        logger.exception("order_agent: beklenmeyen hata")
        return {
            "tool_results": [],
            "final_response": EMPTY_REPLY,
            "order_step": "idle",
            "order_draft": {},
        }
