"""
Proaktif Tedarik Ajanı — LangGraph node.
product_agent.py ile aynı yapıda: OpenAI function calling + async.

Reaktif mod: kullanıcı "X ürünü için mail hazırla" dediğinde çalışır.
Proaktif mod: scheduler/supply_checker.py tarafından tetiklenir (bu dosyadan bağımsız).
"""

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
from tools.supply_tools import (
    get_dusuk_stok_urunler,
    get_urun,
    urun_analiz_et,
    mock_mail_kaydet,
)

load_dotenv()

logger = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(".env içinde OPENAI_API_KEY tanımlı değil.")
        _client = AsyncOpenAI(api_key=key)
    return _client


# ── SYSTEM PROMPT'LAR ─────────────────────────────────────────────────────────

TOOL_CHOICE_SYSTEM = """Sen bir tedarik uzmanısın. Kullanıcının mesajına göre uygun tool'u seç.

KURAL:
- "X ürünü için mail hazırla / sipariş ver / tedarik et" → supply_mail_hazirla
- "Kritik stoklar / hangi ürünler az / stok raporu" → kritik_stok_listele
- Ürün adı belirtilmeden genel tedarik sorusu → kritik_stok_listele

Tool çalıştır; bu turda kullanıcıya doğal dil yanıtı verme."""

MAIL_SYSTEM = """Sen bir kadın kooperatifi adına tedarikçiye mail yazan uzmansın.

KURALLAR:
- Samimi ama profesyonel Türkçe kullan
- "Sayın [Tedarikçi/Kooperatif]," ile başla
- Mevcut stok, satış hızı ve önerilen miktarı net belirt
- Stok tedarik süresinden önce bitiyorsa bunu vurgula (acil)
- "Saygılarımızla, [Kooperatif Adı]" ile bitir
- Mail 4-6 cümle olsun, gereksiz uzatma
- İlk satır: "KONU: [kısa konu]" formatında"""

FINAL_ANSWER_SYSTEM = """Sen bir tedarik danışmanısın. Aşağıdaki analiz sonuçlarına göre
satıcıya Türkçe, sıcak ve net bir özet sun.

KURALLAR:
- Kritik ürünleri vurgula (🔴 critical, 🟡 warning)
- Rakamları net söyle (stok, satış hızı, kalan gün)
- Transfer seçeneği varsa mutlaka belirt (💚)
- Mail taslağının hazır olduğunu belirt
- Markdown tablo kullanabilirsin"""


# ── TOOL TANIMLARI ────────────────────────────────────────────────────────────

SUPPLY_AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "supply_mail_hazirla",
            "description": (
                "Belirli bir ürün için stok analizi yapar ve tedarikçiye "
                "mail taslağı hazırlar. Ürün adı veya id ile çalışır."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urun_id": {
                        "type": "integer",
                        "description": "Ürün id'si (biliniyorsa)",
                    },
                    "urun_adi": {
                        "type": "string",
                        "description": "Ürün adı veya anahtar kelime (id bilinmiyorsa)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kritik_stok_listele",
            "description": (
                "Stok eşiğinin altındaki tüm kritik ürünleri listeler. "
                "Genel tedarik durumu soruları için kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kooperatif_id": {
                        "type": "integer",
                        "description": "Belirli bir kooperatife filtrele (opsiyonel)",
                    },
                },
            },
        },
    },
]


# ── TOOL DISPATCH ─────────────────────────────────────────────────────────────

async def _mail_uret(analiz: dict) -> str:
    """Analiz nesnesinden LLM ile mail taslağı üretir."""
    client = _get_client()

    transfer_notu = ""
    if analiz.get("transfer_secenekleri"):
        t = analiz["transfer_secenekleri"][0]
        transfer_notu = (
            f"\n\nNOT: {t['kooperatif_adi']} ({t['bolge']}) bu üründen "
            f"{t['stok']} adet fazla stoğa sahip. "
            f"Dış tedarik yerine kooperatif içi transfer de değerlendirilebilir."
        )

    prompt = (
        f"Kooperatif: {analiz['kooperatif_adi']} ({analiz['bolge']})\n"
        f"Ürün: {analiz['urun_adi']}\n"
        f"Mevcut stok: {analiz['mevcut_stok']} adet\n"
        f"Günlük satış hızı: {analiz['gunluk_satis_hizi']} adet/gün\n"
        f"Stok kalan: {analiz['kalan_gun']} gün\n"
        f"Tedarik süresi: {analiz['lead_time_gun']} gün\n"
        f"Kritiklik: {analiz['kritiklik']}\n"
        f"Önerilen sipariş miktarı: {analiz['onerilen_miktar']} adet\n"
        f"{transfer_notu}"
    )

    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.3,
        messages=cast(
            list[ChatCompletionMessageParam],
            [
                {"role": "system", "content": MAIL_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        ),
    )
    return (resp.choices[0].message.content or "").strip()


async def _dispatch_supply_tool(
    name: str,
    args: dict[str, Any],
    kooperatif_id: int | None,
) -> Any:
    if name == "supply_mail_hazirla":
        urun = None
        if args.get("urun_id"):
            urun = await get_urun(int(args["urun_id"]))
            if kooperatif_id is not None and urun and int(urun.get("kooperatif_id") or 0) != kooperatif_id:
                return {
                    "hata": "Bu ürün sizin kooperatifinize ait değil.",
                    "ipucu": "Kooperatifinize kayıtlı bir ürün seçin.",
                }

        if urun is None and args.get("urun_adi"):
            dusukler = await get_dusuk_stok_urunler(
                kooperatif_id=kooperatif_id,
                limit=100,
            )
            arama = str(args["urun_adi"]).lower()
            for u in dusukler:
                if arama in str(u["ad"]).lower():
                    urun = u
                    break

        if urun is None:
            return {
                "hata": "Ürün bulunamadı veya stok kritik seviyede değil.",
                "ipucu": "Ürün adını daha net belirtebilir misiniz?",
            }

        if kooperatif_id is not None and int(urun.get("kooperatif_id") or 0) != kooperatif_id:
            return {
                "hata": "Bu ürün sizin kooperatifinize ait değil.",
                "ipucu": "Kooperatifinize kayıtlı bir ürün seçin.",
            }

        analiz = await urun_analiz_et(urun)
        mail_metni = await _mail_uret(analiz)

        ilk_satir = mail_metni.split("\n")[0]
        konu = ilk_satir.replace("KONU:", "").strip() if "KONU:" in ilk_satir else f"{urun['ad']} - Tedarik Talebi"
        dosya_yolu = mock_mail_kaydet(str(urun["ad"]), konu, mail_metni)

        return {
            **analiz,
            "mail_metni": mail_metni,
            "mail_konu": konu,
            "dosya_yolu": dosya_yolu,
        }

    if name == "kritik_stok_listele":
        kid = kooperatif_id if kooperatif_id is not None else args.get("kooperatif_id")
        urunler = await get_dusuk_stok_urunler(
            kooperatif_id=int(kid) if kid is not None else None,
            limit=20,
        )
        analizler = []
        for u in urunler[:10]:
            analizler.append(await urun_analiz_et(u))
        return {"toplam_kritik": len(urunler), "analizler": analizler}

    raise ValueError(f"Bilinmeyen tool: {name}")


# ── ANA NODE ─────────────────────────────────────────────────────────────────

async def supply_agent_node(state: AgentState) -> dict[str, Any]:
    """
    LangGraph node. supervisor.py'da build_chat_graph() içinde
    "supply" intent'ine bağlanır.
    """
    try:
        logger.info("supply_agent: başlıyor")
        user_text = last_user_text(state)
        if not user_text:
            return {
                "tool_results": [],
                "final_response": "Hangi ürün için tedarik maili hazırlayayım?",
            }

        kid = state.get("kooperatif_id")
        kooperatif_id = kid if isinstance(kid, int) else None

        client = _get_client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": TOOL_CHOICE_SYSTEM},
            {"role": "user", "content": user_text},
        ]

        tool_results: list[dict[str, Any]] = []

        # Tek tool turu yeterli (tedarik için çok adım gerekmez)
        resp = await client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            messages=cast(list[ChatCompletionMessageParam], messages),
            tools=cast(list[ChatCompletionToolUnionParam], SUPPLY_AGENT_TOOLS),
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        tcalls = getattr(msg, "tool_calls", None) or []

        if not tcalls:
            # Model tool çağırmadıysa doğrudan cevap
            direct = (msg.content or "").strip()
            return {
                "tool_results": [],
                "final_response": direct or "Tedarik konusunda daha fazla bilgi verir misiniz?",
            }

        for tc in tcalls:
            fname = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            logger.info("supply_agent: tool çalıştırılıyor → %s", fname)
            try:
                sonuc = await _dispatch_supply_tool(fname, args, kooperatif_id)
                tool_results.append({"tool": fname, "result": sonuc})
            except Exception:
                logger.exception("supply_agent: tool hatası (%s)", fname)
                tool_results.append({"tool": fname, "result": None})

        # Nihai Türkçe yanıt
        logger.info("supply_agent: nihai yanıt üretiliyor")
        final_resp = await client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.4,
            messages=cast(
                list[ChatCompletionMessageParam],
                [
                    {"role": "system", "content": FINAL_ANSWER_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Kullanıcı sorusu:\n{user_text}\n\n"
                            f"Analiz sonuçları (JSON):\n"
                            f"{json.dumps([r['result'] for r in tool_results], ensure_ascii=False, default=str)}"
                        ),
                    },
                ],
            ),
        )
        final_text = (final_resp.choices[0].message.content or "").strip()
        if not final_text:
            final_text = "Tedarik analizi tamamlandı. Detaylar için dashboard'u kontrol edebilirsiniz."

        logger.info("supply_agent: tamamlandı (len=%s)", len(final_text))
        return {"tool_results": tool_results, "final_response": final_text}

    except RateLimitError as e:
        logger.warning("supply_agent: rate limit")
        raise QuotaExceededError(
            f"OpenAI API kota veya hız limiti. Model: {OPENAI_MODEL}. Detay: {e}"
        ) from e
    except Exception:
        logger.exception("supply_agent: beklenmeyen hata")
        return {
            "tool_results": [],
            "final_response": "Tedarik analizi sırasında bir hata oluştu. Lütfen tekrar deneyin.",
        }
