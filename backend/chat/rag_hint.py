"""
RAG / semantik arama için ilk turda tool_choice ve argüman tamamlama.
"""

from __future__ import annotations

import re

_SEMANTIC_URUN_FORCE = re.compile(
    r"öner|öneri|hediye|armağan|yöresel|organik|el\s*yapımı|el\s*yapimi|doğal|dogal|"
    r"için\s+ne|ne\s+alayım|ne\s+alayim|ne\s+önerirsin|ne\s+önersin",
    re.I,
)

_BOLGE_SUBSTR = re.compile(
    r"hakkari|afyonkarahisar|\bafyon\b|kütahya|kutahya|giresun|denizli|hatay|tokat|kayseri|"
    r"balıkesir|balikesir|adıyaman|adiyaman|çanakkale|canakkale|uşak|usak|tekirdağ|tekirdag|"
    r"ege\s+lezzet",
    re.I,
)


def _canon_bolge(text: str) -> str | None:
    if re.search(r"hakkari", text, re.I):
        return "Hakkari"
    if re.search(r"afyonkarahisar|\bafyon\b", text, re.I):
        return "Afyonkarahisar"
    if re.search(r"kütahya|kutahya", text, re.I):
        return "Kütahya"
    if re.search(r"giresun", text, re.I):
        return "Giresun"
    if re.search(r"denizli", text, re.I):
        return "Denizli"
    if re.search(r"hatay", text, re.I):
        return "Hatay"
    if re.search(r"tokat", text, re.I):
        return "Tokat"
    if re.search(r"kayseri", text, re.I):
        return "Kayseri"
    if re.search(r"balıkesir|balikesir", text, re.I):
        return "Balıkesir"
    if re.search(r"adıyaman|adiyaman", text, re.I):
        return "Adıyaman"
    if re.search(r"çanakkale|canakkale", text, re.I):
        return "Çanakkale"
    if re.search(r"uşak|usak", text, re.I):
        return "Uşak"
    if re.search(r"tekirdağ|tekirdag", text, re.I):
        return "Tekirdağ"
    return None


def infer_bolge_from_message(text: str) -> str | None:
    """Kullanıcı mesajından Chroma metadata ile eşleşecek bölge adı."""
    if not text or not text.strip():
        return None
    low = text.lower()
    has_product_hint = bool(
        re.search(r"ürün|lezzet|kooperatif|hediye|sipariş|siparis", low)
    )
    if _BOLGE_SUBSTR.search(text) and (has_product_hint or "ürün" in low):
        return _canon_bolge(text)
    return None


def customer_should_force_semantic_urun(message: str) -> bool:
    """İlk turda semantic_search_urunler zorlaması."""
    if not message or not message.strip():
        return False
    try:
        if _SEMANTIC_URUN_FORCE.search(message):
            return True
        if re.search(r"ürün", message, re.I) and _BOLGE_SUBSTR.search(message):
            return True
        return False
    except Exception:
        return False


def enrich_semantic_search_urunler_args(latest_user: str, args: dict) -> dict:
    merged = dict(args)
    try:
        q = (merged.get("query") or "").strip()
        if not q:
            merged["query"] = latest_user.strip()
        bolge = infer_bolge_from_message(latest_user)
        if bolge and not (merged.get("bolge") or "").strip():
            merged["bolge"] = bolge
        return merged
    except Exception:
        return merged
