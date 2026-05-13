"""
Model search_products'ı boş veya eksik argümanla çağırdığında kullanıcı mesajından tamamlama.

Örn. "Bal ürünlerinden önerir misin?" → query=bal
"""

from __future__ import annotations

import re

from tools.product_tools import match_canonical_kategori, normalize_kategori_filter

_ORDER_SHIP_RE = re.compile(
    r"sipariş|kargo|takip|numaralı|teslimat",
    re.I,
)

_PRODUCT_ROOTS = (
    "bal",
    "zeytin",
    "peynir",
    "reçel",
    "tekstil",
    "baharat",
    "sirke",
    "çay",
    "kahve",
)


def infer_product_query(message: str) -> str | None:
    text = (message or "").strip()
    if not text or _ORDER_SHIP_RE.search(text):
        return None

    m = re.match(r"(?is)^([\wçğıöşüÇĞİÖŞÜ]+)\s+ürünlerinden\b", text)
    if m:
        return m.group(1).lower()

    low = text.lower()

    if re.search(r"\bvar\s+mı\b", low):
        for root in _PRODUCT_ROOTS:
            if re.search(rf"\b{re.escape(root)}\b", low):
                return root

    if re.search(r"önerir\s*misin|öner\b|önersin\b", low):
        for root in _PRODUCT_ROOTS:
            if re.search(rf"\b{re.escape(root)}\b", low):
                return root

    return None


def infer_category_from_user_message(message: str) -> str | None:
    """Örn. 'el sanatı ürünleriniz' → DB kategori adı."""
    text = (message or "").strip()
    if not text or _ORDER_SHIP_RE.search(text):
        return None
    return match_canonical_kategori(text)


def customer_should_force_product_lookup(message: str) -> bool:
    return infer_product_query(message) is not None or infer_category_from_user_message(message) is not None


def enrich_search_products_args(user_message: str, args: dict) -> dict:
    """Boş query/kategori ise mesajdan tahmin et; kategori çoğul/yazımını düzelt."""
    merged = dict(args)
    q = (merged.get("query") or "").strip()
    kat_in = (merged.get("kategori") or "").strip()

    if kat_in:
        nk = normalize_kategori_filter(kat_in)
        if nk:
            merged["kategori"] = nk

    if q or kat_in:
        if q:
            merged["query"] = q
        return merged

    cat_inf = infer_category_from_user_message(user_message)
    if cat_inf:
        merged["kategori"] = cat_inf
        return merged

    inferred = infer_product_query(user_message)
    if inferred:
        merged["query"] = inferred
    return merged
