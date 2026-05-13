"""
Finance Agent — LLM tabanlı finans yorumlayıcısı.
SQL bilmez. Hesaplama yapmaz. Tool sonucunu yorumlar.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from agents.message_utils import last_user_text
from agents.state import AgentState
from tools.finance_tools import FINANCE_TOOLS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Sen KoopgeLLM'in Finans ve Muhasebe Ajanısın.
Kadın kooperatiflerine satış, ciro ve gider konularında net, güvenilir bilgi sunarsın.

TEMEL KURALLAR:
- Asla kendi başına matematik hesaplama yapma.
- Ciro, kâr, adet, toplam gibi sayısal veriler için mutlaka tool çağır.
- Tool sonucunda veri yoksa tahminde bulunma; "veri bulunamadı" de.
- SQL sorgularını veya teknik detayları kullanıcıya gösterme.
- Sonuçları sade, anlaşılır ve iş odaklı yorumla.
- Para birimini TL olarak belirt.
- Tarihleri Türkçe formatında yaz (örn: 10 Mayıs 2025).

YAPABILECEĞIN İŞLEMLER:
- Günlük ciro sorgulama        → get_daily_revenue
- Haftalık satış özeti         → get_weekly_summary
- En çok satan ürünler         → get_top_selling_products
- En kârlı ürün analizi        → get_profit_by_product
- Bekleyen ödemeler            → get_pending_expenses
"""

# Kullanıcı niyeti → tool adı eşlemesi (Supervisor bunu kullanır)
INTENT_TO_TOOL = {
    "gunluk_ciro":    "get_daily_revenue",
    "haftalik_ozet":  "get_weekly_summary",
    "cok_satan_urun": "get_top_selling_products",
    "karli_urun":     "get_profit_by_product",
    "bekleyen_odeme": "get_pending_expenses",
}


def finance_intent_from_user(text: str) -> str:
    """Supervisor 'finance' döndüğünde alt niyeti kullanıcı metninden çıkarır."""
    t = (text or "").lower()
    if any(w in t for w in ("haftalık", "haftalik", "son 7", "7 gün", "7 gun")):
        return "haftalik_ozet"
    if any(w in t for w in ("en çok satan", "encok satan", "çok satan", "cok satan", "top satış")):
        return "cok_satan_urun"
    if any(w in t for w in ("karlı", "karli", "kârlı", "brüt kar", "brut kar", "karlılık")):
        return "karli_urun"
    if any(w in t for w in ("bekleyen", "ödeme", "odeme", "gider", "fatura")):
        return "bekleyen_odeme"
    return "gunluk_ciro"


async def finance_agent_node(state: AgentState) -> dict[str, Any]:
    """LangGraph düğümü: finans araçlarını çalıştırır, Türkçe metin döner."""
    text = last_user_text(state)
    if not text:
        return {
            "tool_results": [],
            "final_response": (
                "Finans konusunda nasıl yardımcı olabilirim? "
                "Örneğin günlük ciro, haftalık özet veya en çok satan ürünler sorabilirsiniz."
            ),
        }
    sub = finance_intent_from_user(text)
    kid = state.get("kooperatif_id")
    koop_id = kid if isinstance(kid, int) else None
    reply = await run_finance_agent(sub, {}, kooperatif_id=koop_id)
    return {"tool_results": [], "final_response": reply}


async def run_finance_agent(
    intent: str,
    params: dict | None = None,
    kooperatif_id: int | None = None,
) -> str:
    """
    Supervisor'dan gelen intent ve parametreyle doğru tool'u çalıştırır,
    sonucu kullanıcıya uygun Türkçe metne dönüştürür.

    Args:
        intent: INTENT_TO_TOOL içindeki anahtar (örn: "gunluk_ciro")
        params: Tool'a iletilecek ek parametreler (örn: {"date_str": "2025-05-10"})

    Returns:
        Kullanıcıya gösterilecek yorumlanmış metin.
    """
    params = dict(params or {})
    if kooperatif_id is not None:
        params["kooperatif_id"] = kooperatif_id
    tool_name = INTENT_TO_TOOL.get(intent)

    if not tool_name:
        return "Bu konuda finans ajanı devreye giremedi. Lütfen sorunuzu farklı bir şekilde sorun."

    tool_fn = FINANCE_TOOLS.get(tool_name)
    if not tool_fn:
        logger.error("Tool bulunamadı: %s", tool_name)
        return "Finans aracına şu an ulaşılamıyor."

    # Parametreye özel default enjeksiyonu
    if tool_name == "get_daily_revenue" and "date_str" not in params:
        params["date_str"] = date.today().isoformat()

    try:
        raw_data = await tool_fn(**params)
    except Exception:
        logger.exception("Tool çalıştırılamadı: %s", tool_name)
        return "Finansal veriler alınırken bir hata oluştu. Lütfen tekrar deneyin."

    return _format_response(tool_name, raw_data)


def _format_response(tool_name: str, data: Any) -> str:
    """
    Tool çıktısını kullanıcıya uygun Türkçe metne dönüştürür.
    Deterministik şablonlara dayanır — LLM çağrısı yoktur.
    """
    if not data:
        return "Bu konuda kayıt bulunamadı."

    if tool_name == "get_daily_revenue":
        return (
            f"📅 {data['date']} tarihi itibarıyla toplam ciro "
            f"**{data['total_revenue']:,.2f} TL**, "
            f"sipariş sayısı **{data['order_count']}** adet."
        )

    if tool_name == "get_weekly_summary":
        lines = [
            f"📊 Son 7 günde toplam **{data['total_revenue']:,.2f} TL** ciro, "
            f"**{data['total_orders']}** sipariş.\n"
        ]
        for day in data["daily_breakdown"]:
            lines.append(
                f"  • {day['date']}: {day['revenue']:,.2f} TL "
                f"({day['order_count']} sipariş)"
            )
        return "\n".join(lines)

    if tool_name == "get_top_selling_products":
        lines = ["🏆 En çok satan ürünler:\n"]
        for i, p in enumerate(data, 1):
            lines.append(
                f"  {i}. {p['product_name']} — "
                f"{p['total_quantity_sold']} adet / {p['total_revenue']:,.2f} TL"
            )
        return "\n".join(lines)

    if tool_name == "get_profit_by_product":
        lines = ["💰 Ürün bazlı brüt kâr analizi:\n"]
        for p in data:
            lines.append(
                f"  • {p['product_name']}: "
                f"Gelir {p['total_revenue']:,.2f} TL | "
                f"Maliyet {p['total_cost']:,.2f} TL | "
                f"Kâr {p['gross_profit']:,.2f} TL"
            )
        return "\n".join(lines)

    if tool_name == "get_pending_expenses":
        lines = ["⚠️ Bekleyen ödemeler:\n"]
        for e in data:
            due_label = (
                "Bugün!" if e["days_until_due"] == 0
                else f"{e['days_until_due']} gün kaldı"
            )
            lines.append(
                f"  • {e['description']}: {e['amount']:,.2f} TL "
                f"— {e['due_date']} ({due_label})"
            )
        return "\n".join(lines)

    return json.dumps(data, ensure_ascii=False, indent=2)
