"""
Kadın Kooperatifleri AI Asistan — FastAPI uygulaması.

Endpoint'ler:
  POST /auth/login         → kooperatif girişi (JWT)
  POST /auth/logout        → istemci token siler (sunucu tarafsız)
  GET  /auth/me            → Bearer ile oturum özeti
  POST /chat               → { message?, messages?, mode } → { reply }
                            (messages: [{role, content}, ...] çok turnlu sohbet)
  POST /test-supervisor    → { message, mode } → { intent } (LangGraph supervisor testi)
  GET  /products           → ürün listesi (sayfalı)
  GET  /orders/{id}        → sipariş detayı + kalemler + kargo
  GET  /stock/low          → kritik stok altı ürünler
  GET  /health             → DB sağlık kontrolü
  GET  /notifications      → okunmamış tedarik bildirimleri
  POST /notifications/{id}/action → bildirimi onayla
  POST /supply/draft-mail  → tek ürün için mail taslağı
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field, model_validator

import database
from auth.dependencies import get_current_kooperatif
from auth.jwt_handler import create_access_token, decode_token
from auth.password_hash import verify_password
from agents.state import AgentState
from agents.supervisor import chat_graph, supervisor_graph
from chat.conversation import normalize_chat_turns, trim_turns
from chat.errors import QuotaExceededError
from chat.llm_router import (
    langgraph_chat_llm_info,
    standalone_chat_client_info,
)
from tools import order_tools, product_tools, supply_tools
from scheduler.supply_checker import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("kooperatif-ai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    lg = langgraph_chat_llm_info()
    logger.info(
        "LangGraph /chat LLM: %s (openai)",
        lg["model"],
    )
    logger.info("DB pool başlatılıyor...")
    database.init_pool()
    healthy = await database.healthcheck()
    if not healthy:
        logger.warning("DB sağlık kontrolü başarısız! Bağlantı ayarlarını kontrol edin.")
    else:
        logger.info("DB hazır.")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("DB pool kapatılıyor...")
    database.close_pool()


app = FastAPI(
    title="Kadın Kooperatifleri AI Asistan",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── ŞEMALAR ──────────────────────────────────────────────────────────────────

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=32000)


class ChatRequest(BaseModel):
    message: str | None = Field(None, max_length=4000)
    messages: list[ChatTurn] | None = None
    mode: Literal["musteri", "satici"] = "musteri"
    order_step: str | None = Field(None, max_length=64)
    order_draft: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_messages_or_message(self):
        has_m = self.message is not None and len(self.message.strip()) >= 1
        has_list = self.messages is not None and len(self.messages) >= 1
        if not has_m and not has_list:
            raise ValueError("'message' veya dolu bir 'messages' listesi gerekli")
        return self


def _turns_for_llm(req: ChatRequest) -> list[dict[str, str]]:
    if req.messages:
        raw = [{"role": t.role, "content": t.content} for t in req.messages]
        return trim_turns(normalize_chat_turns(raw))
    assert req.message is not None
    return trim_turns(normalize_chat_turns(req.message.strip()))


def _turns_to_lc_messages(turns: list[dict[str, str]]) -> list:
    out: list = []
    for t in turns:
        if t["role"] == "user":
            out.append(HumanMessage(content=t["content"]))
        else:
            out.append(AIMessage(content=t["content"]))
    return out


class ChatResponse(BaseModel):
    reply: str
    mode: str
    order_step: str = "idle"
    order_draft: dict[str, Any] = Field(default_factory=dict)


def _sanitize_order_draft(raw: Any) -> dict[str, Any]:
    """İstemciden gelen sipariş taslağını sınırlı anahtarlarla güvenli hale getirir."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    uid = raw.get("urun_id")
    if uid is not None:
        try:
            out["urun_id"] = int(uid)
        except (TypeError, ValueError):
            pass
    mid = raw.get("miktar")
    if mid is not None:
        try:
            out["miktar"] = int(mid)
        except (TypeError, ValueError):
            pass
    st = raw.get("stok")
    if st is not None:
        try:
            out["stok"] = int(st)
        except (TypeError, ValueError):
            pass
    ad = raw.get("ad")
    if isinstance(ad, str) and ad.strip():
        out["ad"] = ad.strip()[:500]
    birim = raw.get("birim")
    if isinstance(birim, str) and birim.strip():
        out["birim"] = birim.strip()[:64]
    bf = raw.get("birim_fiyat")
    if bf is not None:
        try:
            out["birim_fiyat"] = float(bf)
        except (TypeError, ValueError):
            pass
    return out


class TestSupervisorRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    mode: Literal["musteri", "satici"] = "musteri"


class TestSupervisorResponse(BaseModel):
    intent: str


class LoginRequest(BaseModel):
    kooperatif_adi: str = Field(..., min_length=1, max_length=500)
    sifre: str = Field(..., min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    kooperatif_id: int
    kooperatif_adi: str


# ── ENDPOINT'LER ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    db_ok = await database.healthcheck()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "langgraph_chat": langgraph_chat_llm_info(),
        "standalone_chat_client": standalone_chat_client_info(),
    }


@app.post("/auth/login", response_model=LoginResponse)
async def auth_login(body: LoginRequest):
    arama = body.kooperatif_adi.strip()
    row = await database.fetch_one(
        """
        SELECT id, ad, sifre_hash, aktif
        FROM kooperatifler
        WHERE ad ILIKE %s
        ORDER BY char_length(ad) ASC
        LIMIT 1
        """,
        [f"%{arama}%"],
    )
    if not row or not row.get("sifre_hash"):
        raise HTTPException(status_code=401, detail="Kooperatif bulunamadı")
    if row.get("aktif") is False:
        raise HTTPException(status_code=403, detail="Hesap aktif değil")
    if not verify_password(body.sifre, str(row["sifre_hash"])):
        raise HTTPException(status_code=401, detail="Hatalı şifre")

    kid = int(row["id"])
    ad = str(row["ad"])
    token = create_access_token(kid, ad)
    return LoginResponse(
        access_token=token,
        token_type="bearer",
        kooperatif_id=kid,
        kooperatif_adi=ad,
    )


@app.post("/auth/logout")
async def auth_logout():
    """İstemci yerelde token'ı siler; sunucu tarafsız."""
    return {"ok": True}


@app.get("/auth/me")
async def auth_me(payload: dict[str, Any] = Depends(get_current_kooperatif)):
    return {
        "kooperatif_id": payload["kooperatif_id"],
        "kooperatif_adi": payload.get("kooperatif_adi"),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    req: ChatRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    turns = _turns_for_llm(req)
    if not turns:
        raise HTTPException(status_code=400, detail="Geçerli sohbet içeriği yok")
    if turns[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="Son mesaj kullanıcıdan olmalıdır")

    last_u = turns[-1]["content"]
    preview = last_u if len(last_u) <= 500 else last_u[:500] + "…"
    logger.info(
        "Chat istek mode=%s turns=%s son_kullanıcı=%r",
        req.mode,
        len(turns),
        preview,
    )

    kooperatif_id: int | None = None
    if req.mode == "satici":
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Satıcı modu için login gerekli")
        tok = authorization.removeprefix("Bearer ").strip()
        if not tok:
            raise HTTPException(status_code=401, detail="Satıcı modu için login gerekli")
        payload = decode_token(tok)
        try:
            kooperatif_id = int(payload["kooperatif_id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=401, detail="Geçersiz token")

    try:
        lc_messages = _turns_to_lc_messages(turns)
        step_in = (req.order_step or "idle").strip() or "idle"
        draft_in = _sanitize_order_draft(req.order_draft)
        initial: AgentState = {
            "messages": lc_messages,
            "mode": req.mode,
            "intent": "",
            "tool_results": [],
            "final_response": "",
            "order_step": step_in,
            "order_draft": draft_in,
            "kooperatif_id": kooperatif_id,
        }
        result = await chat_graph.ainvoke(initial)
        reply = (result.get("final_response") or "").strip()
        if not reply:
            reply = "Şu anda yanıt oluşturulamadı. Lütfen tekrar dener misin?"
        reply_preview = reply if len(reply) <= 600 else reply[:600] + "…"
        logger.info(
            "Chat yanıt mode=%s len=%s önizleme=%r",
            req.mode,
            len(reply),
            reply_preview,
        )
        out_step = (result.get("order_step") or "idle").strip() or "idle"
        out_draft = result.get("order_draft")
        if not isinstance(out_draft, dict):
            out_draft = {}
        return ChatResponse(
            reply=reply,
            mode=req.mode,
            order_step=out_step,
            order_draft=_sanitize_order_draft(out_draft),
        )
    except QuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Chat hatası")
        raise HTTPException(status_code=500, detail=f"Chat hatası: {e}")


@app.post("/test-supervisor", response_model=TestSupervisorResponse)
async def test_supervisor_endpoint(req: TestSupervisorRequest):
    """LangGraph supervisor yalnızca intent üretir; alt ajan yok."""
    initial: AgentState = {
        "messages": [HumanMessage(content=req.message.strip())],
        "mode": req.mode,
        "intent": "",
        "tool_results": [],
        "final_response": "",
        "order_step": "idle",
        "order_draft": {},
        "kooperatif_id": None,
    }
    try:
        out = await supervisor_graph.ainvoke(initial)
        intent = (out.get("intent") or "unknown").strip().lower()
        if intent not in ("product", "stock", "finance", "order", "supply", "unknown"):
            intent = "unknown"
        return TestSupervisorResponse(intent=intent)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("test-supervisor hatası")
        raise HTTPException(status_code=500, detail=f"Supervisor hatası: {e}")


@app.get("/products")
async def products(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    kategori: str | None = None,
    q: str | None = None,
):
    if q or kategori:
        return await product_tools.search_products(query=q, kategori=kategori, limit=limit)
    return await product_tools.list_products(limit=limit, offset=offset)


@app.get("/orders/{order_id}")
async def order_detail(order_id: int):
    siparis = await order_tools.get_order(order_id)
    if not siparis:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    kalemler = await order_tools.get_order_items(order_id)
    kargo = await order_tools.get_shipping_info(order_id)
    return {"siparis": siparis, "kalemler": kalemler, "kargo": kargo}


@app.get("/stock/low")
async def low_stock(
    kooperatif_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    items = await product_tools.get_low_stock(kooperatif_id=kooperatif_id, limit=limit)
    return {"toplam": len(items), "urunler": items}


# ── TEDARİK ENDPOINT'LERİ ─────────────────────────────────────────────────────

@app.get("/notifications")
async def notifications(okunmamis: bool = True):
    """Frontend polling için okunmamış bildirimleri döner (badge sayısı)."""
    items = await supply_tools.get_bildirimler(okunmamis_sadece=okunmamis)
    return {"toplam": len(items), "items": items}


@app.post("/notifications/{bildirim_id}/action")
async def notification_action(bildirim_id: int):
    """Satıcı 'Onayla ve Gönder' butonuna bastığında çağrılır."""
    bildirim = await database.fetch_one(
        "SELECT * FROM bildirimler WHERE id = %s", (bildirim_id,)
    )
    if not bildirim:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı")
    dosya_yolu = bildirim.get("mail_dosya_yolu")
    await supply_tools.bildirim_aksiyon_al(bildirim_id, dosya_yolu)
    return {"ok": True, "mesaj": "Mail onaylandı ve gönderildi (mock)"}


class DraftMailRequest(BaseModel):
    urun_id: int | None = None
    urun_adi: str | None = None


@app.post("/supply/draft-mail")
async def supply_draft_mail(req: DraftMailRequest):
    """Tek ürün için reaktif mail taslağı (satıcı manuel tetikler)."""
    if not req.urun_id and not req.urun_adi:
        raise HTTPException(status_code=400, detail="urun_id veya urun_adi gerekli")

    urun = None
    if req.urun_id:
        urun = await supply_tools.get_urun(req.urun_id)
    if urun is None and req.urun_adi:
        kritikler = await supply_tools.get_dusuk_stok_urunler(limit=100)
        arama = req.urun_adi.lower()
        for u in kritikler:
            if arama in u["ad"].lower():
                urun = u
                break

    if urun is None:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")

    from agents.supply_agent import _mail_uret
    analiz = await supply_tools.urun_analiz_et(urun)
    mail_metni = await _mail_uret(analiz)

    ilk_satir = mail_metni.split("\n")[0]
    konu = ilk_satir.replace("KONU:", "").strip() if "KONU:" in ilk_satir else f"{urun['ad']} - Tedarik Talebi"
    dosya_yolu = supply_tools.mock_mail_kaydet(str(urun["ad"]), konu, mail_metni)

    return {
        "analiz": analiz,
        "mail_konu": konu,
        "mail_metni": mail_metni,
        "dosya_yolu": dosya_yolu,
    }
