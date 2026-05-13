"""
Ürün ile ilgili SQL fonksiyonları.
Hepsi async; psycopg2 çağrıları database.py'da to_thread'e devredilir.
"""

from __future__ import annotations

import re
from typing import Any

from database import fetch_all, fetch_one

# Modelın çoğul / yanlış yazdığı kategori ifadelerini DB'deki sabit adlara çeker.
_KATEGORI_CANON_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"el\s+sanat", re.I), "El Sanatı"),
    (re.compile(r"kahvalt", re.I), "Kahvaltılık"),
    (re.compile(r"bakliyat", re.I), "Bakliyat"),
    (re.compile(r"zeytin\s*yağı|zeytinyagi|zeytinyağı", re.I), "Zeytinyağı"),
    (re.compile(r"tursu|turşu", re.I), "Turşu"),
    (re.compile(r"baharat", re.I), "Baharat"),
    (re.compile(r"kişisel\s+bak|kisisel\s+bak", re.I), "Kişisel Bakım"),
]


def match_canonical_kategori(text: str) -> str | None:
    """Metinde bilinen bir kategori geçiyorsa DB'deki tam adı döner."""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()
    for pat, canonical in _KATEGORI_CANON_PATTERNS:
        if pat.search(raw):
            return canonical
    return None


def normalize_kategori_filter(kategori: str | None) -> str | None:
    """Tool'un gönderdiği kategori stringini DB ile uyumlu hale getirir."""
    if not kategori or not str(kategori).strip():
        return None
    raw = str(kategori).strip()
    hit = match_canonical_kategori(raw)
    return hit if hit is not None else raw


async def search_products(
    query: str | None = None,
    kategori: str | None = None,
    min_fiyat: float | None = None,
    max_fiyat: float | None = None,
    kooperatif_id: int | None = None,
    kooperatif_ad: str | None = None,
    bolge: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ürünleri ad / kategori / fiyat / bölge / kooperatif filtresiyle arar."""
    kategori = normalize_kategori_filter(kategori)

    sql = """
        SELECT u.id, u.ad, u.kategori, u.birim, u.fiyat, u.stok,
               u.aciklama, k.ad AS kooperatif_ad, k.bolge
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE 1=1
    """
    params: list[Any] = []

    if query:
        sql += " AND (u.ad ILIKE %s OR u.aciklama ILIKE %s OR u.kategori ILIKE %s)"
        like = f"%{query}%"
        params.extend([like, like, like])

    if kategori:
        sql += " AND u.kategori ILIKE %s"
        params.append(kategori)

    if min_fiyat is not None:
        sql += " AND u.fiyat >= %s"
        params.append(min_fiyat)

    if max_fiyat is not None:
        sql += " AND u.fiyat <= %s"
        params.append(max_fiyat)

    if kooperatif_id is not None:
        sql += " AND u.kooperatif_id = %s"
        params.append(kooperatif_id)

    if kooperatif_ad and str(kooperatif_ad).strip():
        sql += " AND k.ad ILIKE %s"
        params.append(f"%{kooperatif_ad.strip()}%")

    if bolge and str(bolge).strip():
        sql += " AND k.bolge ILIKE %s"
        params.append(f"%{bolge.strip()}%")

    sql += " ORDER BY u.stok DESC, u.fiyat ASC LIMIT %s"
    params.append(limit)

    return await fetch_all(sql, params)


async def get_product_detail(
    product_id: int,
    kooperatif_id: int | None = None,
) -> dict[str, Any] | None:
    """Tek bir ürünün tüm bilgilerini + bağlı kooperatifi döndürür."""
    sql = """
        SELECT u.*, k.ad AS kooperatif_ad, k.bolge AS kooperatif_bolge,
               k.telefon AS kooperatif_telefon, k.email AS kooperatif_email
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.id = %s
    """
    params: list[Any] = [product_id]
    if kooperatif_id is not None:
        sql += " AND u.kooperatif_id = %s"
        params.append(kooperatif_id)
    return await fetch_one(sql, params)


async def list_categories() -> list[dict[str, Any]]:
    """Tüm kategorileri ve her kategorideki ürün sayısını döndürür."""
    sql = """
        SELECT kategori, COUNT(*) AS urun_sayisi
        FROM urunler
        GROUP BY kategori
        ORDER BY urun_sayisi DESC
    """
    return await fetch_all(sql)


async def get_low_stock(
    kooperatif_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Stoğu kritik eşiğin altında veya eşit olan ürünleri listeler.
    SATICI MODU için kullanılır.
    """
    sql = """
        SELECT u.id, u.ad, u.kategori, u.stok, u.kritik_esik,
               u.fiyat, k.ad AS kooperatif_ad
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.stok <= u.kritik_esik
    """
    params: list[Any] = []

    if kooperatif_id is not None:
        sql += " AND u.kooperatif_id = %s"
        params.append(kooperatif_id)

    sql += " ORDER BY (u.stok::float / NULLIF(u.kritik_esik, 0)) ASC LIMIT %s"
    params.append(limit)

    return await fetch_all(sql, params)


async def get_stock_by_product(
    urun_adi: str,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """Ürün adına göre stok satırları (ILIKE); sayısal veri yalnızca DB'den."""
    like = f"%{(urun_adi or '').strip()}%"
    params: list[Any] = [like]
    coop_clause = ""
    if kooperatif_id is not None:
        coop_clause = " AND u.kooperatif_id = %s"
        params.append(kooperatif_id)
    sql = f"""
        SELECT u.ad, u.stok, u.kritik_esik, u.kategori, u.kooperatif_id,
               k.ad AS kooperatif_ad
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.ad ILIKE %s
        {coop_clause}
        ORDER BY u.stok ASC
        LIMIT 50
    """
    return await fetch_all(sql, params)


async def get_low_stock_list(
    kooperatif_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Kritik eşik altı veya eşit stoktaki ürünler (satıcı envanteri)."""
    return await get_low_stock(kooperatif_id=kooperatif_id, limit=limit)


async def get_products_stok_below(
    max_stok_exclusive: int = 10,
    limit: int = 100,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Stok belirtilen değerden küçük ürünler (örn. 'tükeniyor' için max_stok_exclusive=10 → stok < 10).
    """
    params: list[Any] = [max_stok_exclusive]
    coop_clause = ""
    if kooperatif_id is not None:
        coop_clause = " AND u.kooperatif_id = %s"
        params.append(kooperatif_id)
    params.append(limit)
    sql = f"""
        SELECT u.ad, u.stok, u.kritik_esik, u.kategori, u.kooperatif_id,
               k.ad AS kooperatif_ad
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.stok < %s
        {coop_clause}
        ORDER BY u.stok ASC
        LIMIT %s
    """
    return await fetch_all(sql, params)


async def list_coop_inventory(
    kooperatif_id: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Satıcı paneli: tek kooperatifin tüm ürünleri — stok, kritik eşik (stok 0 dahil)."""
    sql = """
        SELECT u.id, u.ad, u.kategori, u.birim, u.fiyat, u.stok, u.kritik_esik,
               k.ad AS kooperatif_ad
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.kooperatif_id = %s
        ORDER BY
          CASE WHEN u.stok <= u.kritik_esik THEN 0 ELSE 1 END,
          u.stok ASC,
          u.ad ASC
        LIMIT %s
    """
    return await fetch_all(sql, [kooperatif_id, limit])


async def get_stock_by_name(product_name: str) -> list[dict[str, Any]]:
    """Ürün adıyla stok sorgular (esnek arama)."""
    sql = """
        SELECT u.id, u.ad, u.stok, u.kritik_esik, u.birim,
               k.ad AS kooperatif_ad
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.ad ILIKE %s
        ORDER BY u.stok ASC
        LIMIT 20
    """
    return await fetch_all(sql, [f"%{product_name}%"])


async def list_products(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """REST endpoint /products için sayfalı liste."""
    sql = """
        SELECT u.id, u.ad, u.kategori, u.birim, u.fiyat, u.stok,
               u.aciklama, k.ad AS kooperatif_ad, k.bolge
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        ORDER BY u.id ASC
        LIMIT %s OFFSET %s
    """
    return await fetch_all(sql, [limit, offset])
