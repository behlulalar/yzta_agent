"""LangGraph supervisor + ürün ajanı yönlendirmesi; test için ayrı intent-only graph."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from openai import AsyncOpenAI

from agents.message_utils import last_user_text
from agents.finance_agent import finance_agent_node
from agents.order_agent import order_agent_node
from agents.product_agent import product_agent_node
from agents.stock_agent import stock_agent_node
from agents.supply_agent import supply_agent_node
from agents.state import AgentState

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

INTENT_LABELS = frozenset({"product", "stock", "finance", "order", "supply", "unknown"})

_SELLER_MODE_NAMES = frozenset({"satici", "seller", "vendor"})
_SHORT_STOCK_CONTINUE = frozenset({"bütün", "butun", "tümü", "tumu", "hepsi", "tüm", "tum", "all"})
_RE_SELLER_STOCK_HINT = re.compile(
    r"stok|envanter|kritik|eşik|esik|tüken|tuken|depoda|depom|"
    r"miktar|ürünler.+listele|listele.+(stok|ürün)|stok.+liste|"
    r"ürünlerimi|urunlerimi|ürünlerin|urunlerin|hangi ürün|rünler",
    re.I,
)


def _seller_should_route_stock(mode: str | None, text: str) -> bool:
    if (mode or "").strip().lower() not in _SELLER_MODE_NAMES:
        return False
    t = (text or "").strip().lower()
    if len(t) <= 32 and t in _SHORT_STOCK_CONTINUE:
        return True
    return bool(_RE_SELLER_STOCK_HINT.search(text or ""))


INTENT_SYSTEM_PROMPT = """Sen bir intent sınıflandırıcısısın. Kullanıcı mesajına göre yalnızca şu etiketlerden BİRİNİ seç ve yanıtta SADECE o kelimeyi küçük harfle yaz (başka metin yok).

- product: ürün arama, öneri, hediye, kategori, "var mı" ile ürün sorma
- stock: stok miktarı, envanter, kritik stok, depoda ne kadar, ürünlerin stok listesi, stok durumu
- finance: ciro, kâr, muhasebe, gelir, gider, ne kadar kazandık
- order: sipariş durumu, kargo, teslimat, siparişim nerede, sipariş vermek istiyorum, satın almak istiyorum, almak istiyorum
- supply: tedarik, tedarikçi, tedarikçiye mail/mesaj yaz, tedarikçiden sipariş ver
- unknown: yukarıdakilerin hiçbiri net değilse
"""

_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env içinde OPENAI_API_KEY tanımlayın.")
        _client = AsyncOpenAI(api_key=key)
    return _client


def _normalize_intent(raw: str) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return "unknown"
    match = re.search(r"\b(product|stock|finance|order|supply|unknown)\b", text)
    if match:
        return match.group(1)
    if text in INTENT_LABELS:
        return text
    return "unknown"


async def _classify_intent_openai(user_message: str) -> str:
    client = _get_openai_client()
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    choice = resp.choices[0].message.content or ""
    return _normalize_intent(choice)


async def supervisor_node(state: AgentState) -> dict[str, Any]:
    """Son kullanıcı mesajından intent üretir; state güncellemesi döner."""
    try:
        step_raw = state.get("order_step") or "idle"
        step = str(step_raw).strip().lower()
        if step and step != "idle":
            logger.info(
                "Supervisor: sipariş oluşturma akışı sürüyor (order_step=%s) → intent=order",
                step_raw,
            )
            return {"intent": "order"}

        user_text = last_user_text(state)
        if not user_text:
            logger.info("Intent tespit edildi: %s", "unknown")
            return {"intent": "unknown"}

        mode_raw = state.get("mode")
        if _seller_should_route_stock(str(mode_raw) if mode_raw is not None else None, user_text):
            logger.info("Supervisor: satıcı stok/envanter heuristic → intent=stock")
            return {"intent": "stock"}

        intent = await _classify_intent_openai(user_text)
        if intent not in INTENT_LABELS:
            intent = "unknown"

        logger.info("Intent tespit edildi: %s", intent)
        return {"intent": intent}
    except Exception:
        logger.exception("Supervisor node hatası")
        logger.info("Intent tespit edildi: %s", "unknown")
        return {"intent": "unknown"}


def route_intent(state: AgentState) -> str:
    """Alt graf yönlendirmesi için intent anahtarı; unknown ayrı kalır."""
    raw = (state.get("intent") or "unknown").strip().lower()
    if raw not in INTENT_LABELS:
        return "unknown"
    return raw


def build_supervisor_graph():
    """Yalnızca supervisor → END (/test-supervisor için)."""
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.set_entry_point("supervisor")
    g.add_edge("supervisor", END)
    return g.compile()


def build_chat_graph():
    """Supervisor → ürün, stok, sipariş, finans, tedarik ajanları → END."""
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("product_agent", product_agent_node)
    g.add_node("stock_agent", stock_agent_node)
    g.add_node("order_agent", order_agent_node)
    g.add_node("supply_agent", supply_agent_node)
    g.add_node("finance_agent", finance_agent_node)
    g.set_entry_point("supervisor")
    g.add_conditional_edges(
        "supervisor",
        route_intent,
        {
            "product": "product_agent",
            "unknown": "product_agent",
            "stock":   "stock_agent",
            "finance": "finance_agent",
            "order":   "order_agent",
            "supply":  "supply_agent",
        },
    )
    g.add_edge("product_agent", END)
    g.add_edge("stock_agent", END)
    g.add_edge("order_agent", END)
    g.add_edge("supply_agent", END)
    g.add_edge("finance_agent", END)
    return g.compile()


supervisor_graph = build_supervisor_graph()
chat_graph = build_chat_graph()
