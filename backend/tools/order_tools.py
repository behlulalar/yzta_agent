"""
Sipariş, kargo ve raporlama ile ilgili SQL fonksiyonları.
Hepsi async; iş mantığı yok.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal
from typing import Any

from database import fetch_all, fetch_one, get_conn
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


async def get_order(
    order_id: int,
    kooperatif_id: int | None = None,
) -> dict[str, Any] | None:
    """Sipariş özeti + müşteri bilgisi."""
    sql = """
        SELECT s.id, s.toplam_tutar, s.durum, s.kargo_firma, s.kargo_takip_no,
               s.kargo_durum, s.adres, s.notlar, s.olusturulma, s.guncelleme,
               m.ad_soyad AS musteri_ad, m.email AS musteri_email,
               m.telefon AS musteri_telefon, m.sehir AS musteri_sehir
        FROM siparisler s
        JOIN musteriler m ON m.id = s.musteri_id
        WHERE s.id = %s
    """
    params: list[Any] = [order_id]
    if kooperatif_id is not None:
        sql += """
          AND EXISTS (
            SELECT 1 FROM siparis_kalemleri sk
            JOIN urunler u ON u.id = sk.urun_id
            WHERE sk.siparis_id = s.id AND u.kooperatif_id = %s
          )
        """
        params.append(kooperatif_id)
    return await fetch_one(sql, params)


async def get_order_items(
    order_id: int,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """Bir siparişin satır kalemleri (ürün adı + miktar + fiyat)."""
    sql = """
        SELECT sk.id, sk.miktar, sk.birim_fiyat,
               (sk.miktar * sk.birim_fiyat) AS satir_toplami,
               u.id AS urun_id, u.ad AS urun_ad, u.kategori, u.birim,
               k.ad AS kooperatif_ad
        FROM siparis_kalemleri sk
        JOIN urunler u ON u.id = sk.urun_id
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE sk.siparis_id = %s
    """
    params: list[Any] = [order_id]
    if kooperatif_id is not None:
        sql += " AND u.kooperatif_id = %s"
        params.append(kooperatif_id)
    sql += " ORDER BY sk.id"
    return await fetch_all(sql, params)


async def get_shipping_info(
    order_id: int,
    kooperatif_id: int | None = None,
) -> dict[str, Any] | None:
    """Sadece kargo bilgileri (takip için)."""
    sql = """
        SELECT s.id, s.kargo_firma, s.kargo_takip_no, s.kargo_durum,
               s.durum AS siparis_durumu, s.adres, s.guncelleme
        FROM siparisler s
        WHERE s.id = %s
    """
    params: list[Any] = [order_id]
    if kooperatif_id is not None:
        sql += """
          AND EXISTS (
            SELECT 1 FROM siparis_kalemleri sk
            JOIN urunler u ON u.id = sk.urun_id
            WHERE sk.siparis_id = s.id AND u.kooperatif_id = %s
          )
        """
        params.append(kooperatif_id)
    return await fetch_one(sql, params)


async def list_recent_orders(
    limit: int = 10,
    durum: str | None = None,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """Son siparişler; opsiyonel durum filtresiyle."""
    sql = """
        SELECT s.id, s.toplam_tutar, s.durum, s.kargo_durum,
               s.olusturulma, m.ad_soyad AS musteri_ad
        FROM siparisler s
        JOIN musteriler m ON m.id = s.musteri_id
        WHERE 1=1
    """
    params: list[Any] = []

    if kooperatif_id is not None:
        sql += """
          AND EXISTS (
            SELECT 1 FROM siparis_kalemleri sk
            JOIN urunler u ON u.id = sk.urun_id
            WHERE sk.siparis_id = s.id AND u.kooperatif_id = %s
          )
        """
        params.append(kooperatif_id)

    if durum:
        sql += " AND s.durum = %s"
        params.append(durum)

    sql += " ORDER BY s.olusturulma DESC LIMIT %s"
    params.append(limit)

    return await fetch_all(sql, params)


async def get_daily_report(
    target_date: str | None = None,
    kooperatif_id: int | None = None,
) -> dict[str, Any]:
    """
    Belirli bir gün için satış raporu.
    target_date: 'YYYY-MM-DD'; None ise bugünü kullanır.
    kooperatif_id: Satıcı modunda yalnızca bu kooperatifin ürün kalemleri.
    """
    gun = target_date or date.today().isoformat()

    if kooperatif_id is None:
        ozet = await fetch_one(
            """
            SELECT
                COUNT(*) AS siparis_sayisi,
                COUNT(*) FILTER (WHERE durum = 'teslim edildi') AS teslim_edilen,
                COUNT(*) FILTER (WHERE durum = 'iptal') AS iptal,
                COALESCE(SUM(toplam_tutar) FILTER (WHERE durum != 'iptal'), 0) AS toplam_ciro
            FROM siparisler
            WHERE DATE(olusturulma) = %s
            """,
            [gun],
        )

        en_cok_satan = await fetch_all(
            """
            SELECT u.id, u.ad, SUM(sk.miktar) AS toplam_miktar,
                   SUM(sk.miktar * sk.birim_fiyat) AS toplam_gelir
            FROM siparis_kalemleri sk
            JOIN siparisler s ON s.id = sk.siparis_id
            JOIN urunler u ON u.id = sk.urun_id
            WHERE DATE(s.olusturulma) = %s AND s.durum != 'iptal'
            GROUP BY u.id, u.ad
            ORDER BY toplam_miktar DESC
            LIMIT 5
            """,
            [gun],
        )
    else:
        ozet = await fetch_one(
            """
            SELECT
                COUNT(DISTINCT s.id) AS siparis_sayisi,
                COUNT(DISTINCT s.id) FILTER (WHERE s.durum = 'teslim edildi') AS teslim_edilen,
                COUNT(DISTINCT s.id) FILTER (WHERE s.durum = 'iptal') AS iptal,
                COALESCE(
                    SUM(sk.miktar * sk.birim_fiyat) FILTER (WHERE s.durum != 'iptal'),
                    0
                ) AS toplam_ciro
            FROM siparisler s
            JOIN siparis_kalemleri sk ON sk.siparis_id = s.id
            JOIN urunler u ON u.id = sk.urun_id
            WHERE DATE(s.olusturulma) = %s AND u.kooperatif_id = %s
            """,
            [gun, kooperatif_id],
        )

        en_cok_satan = await fetch_all(
            """
            SELECT u.id, u.ad, SUM(sk.miktar) AS toplam_miktar,
                   SUM(sk.miktar * sk.birim_fiyat) AS toplam_gelir
            FROM siparis_kalemleri sk
            JOIN siparisler s ON s.id = sk.siparis_id
            JOIN urunler u ON u.id = sk.urun_id
            WHERE DATE(s.olusturulma) = %s AND s.durum != 'iptal'
              AND u.kooperatif_id = %s
            GROUP BY u.id, u.ad
            ORDER BY toplam_miktar DESC
            LIMIT 5
            """,
            [gun, kooperatif_id],
        )

    return {
        "tarih": gun,
        "ozet": ozet or {},
        "en_cok_satan_urunler": en_cok_satan,
    }


async def search_orders_by_customer(
    musteri_email: str | None = None,
    musteri_ad: str | None = None,
    limit: int = 10,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """Müşteri email veya ada göre siparişleri arar."""
    if not musteri_email and not musteri_ad:
        return []

    sql = """
        SELECT s.id, s.toplam_tutar, s.durum, s.kargo_durum,
               s.olusturulma, m.ad_soyad, m.email
        FROM siparisler s
        JOIN musteriler m ON m.id = s.musteri_id
        WHERE 1=1
    """
    params: list[Any] = []

    if kooperatif_id is not None:
        sql += """
          AND EXISTS (
            SELECT 1 FROM siparis_kalemleri sk
            JOIN urunler u ON u.id = sk.urun_id
            WHERE sk.siparis_id = s.id AND u.kooperatif_id = %s
          )
        """
        params.append(kooperatif_id)

    if musteri_email:
        sql += " AND m.email ILIKE %s"
        params.append(f"%{musteri_email}%")

    if musteri_ad:
        sql += " AND m.ad_soyad ILIKE %s"
        params.append(f"%{musteri_ad}%")

    sql += " ORDER BY s.olusturulma DESC LIMIT %s"
    params.append(limit)

    return await fetch_all(sql, params)


async def get_product_for_order(
    urun_adi: str,
    kooperatif_id: int | None = None,
) -> dict[str, Any]:
    """Sipariş için tek ürün satırı (stokta olmalı). Bulunamazsa hata sözlüğü."""
    like = f"%{(urun_adi or '').strip()}%"
    if kooperatif_id is None:
        row = await fetch_one(
            """
            SELECT id, ad, fiyat, stok, birim
            FROM urunler
            WHERE ad ILIKE %s
              AND stok > 0
            ORDER BY stok DESC
            LIMIT 1
            """,
            [like],
        )
    else:
        row = await fetch_one(
            """
            SELECT id, ad, fiyat, stok, birim
            FROM urunler
            WHERE ad ILIKE %s
              AND stok > 0
              AND kooperatif_id = %s
            ORDER BY stok DESC
            LIMIT 1
            """,
            [like, kooperatif_id],
        )
    if not row:
        return {"error": "Ürün bulunamadı veya stokta yok"}
    return dict(row)


def _create_order_transaction(
    urun_id: int,
    miktar: int,
    musteri_adi: str,
    adres: str,
    mock_musteri_id: int = 1,
    kooperatif_id: int | None = None,
) -> dict[str, Any]:
    """Tek transaction: sipariş + kalem + stok güncelleme; hata durumunda rollback."""
    if miktar < 1:
        return {"error": "Miktar en az 1 olmalıdır."}
    adr = (adres or "").strip()
    if not adr:
        return {"error": "Adres boş olamaz. Teslimat adresinizi paylaşır mısınız?"}

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, ad, fiyat, stok, kooperatif_id
                FROM urunler
                WHERE id = %s
                FOR UPDATE
                """,
                (urun_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": "Ürün bulunamadı."}
            if kooperatif_id is not None:
                cid = row.get("kooperatif_id")
                if cid is None or int(cid) != kooperatif_id:
                    return {"error": "Bu ürün sizin kooperatifinize ait değil."}
            stok = int(row["stok"])
            if stok < miktar:
                logger.info(
                    "Sipariş iptal: yetersiz stok (urun_id=%s istenen=%s stok=%s)",
                    urun_id,
                    miktar,
                    stok,
                )
                return {
                    "error": f"Üzgünüm, stokta sadece {stok} adet var.",
                    "stok_kalan": stok,
                }

            ad = row["ad"]
            birim_fiyat_dec = Decimal(str(row["fiyat"]))
            toplam_dec = (birim_fiyat_dec * miktar).quantize(Decimal("0.01"))

            cur.execute(
                """
                INSERT INTO siparisler
                    (musteri_id, toplam_tutar, durum, adres, olusturulma)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (mock_musteri_id, str(toplam_dec), "beklemede", adr),
            )
            sid_row = cur.fetchone()
            siparis_id = int(sid_row["id"])

            cur.execute(
                """
                INSERT INTO siparis_kalemleri
                    (siparis_id, urun_id, miktar, birim_fiyat)
                VALUES (%s, %s, %s, %s)
                """,
                (siparis_id, urun_id, miktar, str(birim_fiyat_dec)),
            )

            cur.execute(
                "UPDATE urunler SET stok = stok - %s WHERE id = %s",
                (miktar, urun_id),
            )

    toplam_float = float(toplam_dec)
    mock_url = (
        f"https://odeme.kadinkooperatifleri.com/pay?order={siparis_id}&tutar={toplam_float}"
    )
    logger.info(
        "Sipariş oluşturuldu: siparis_id=%s urun_id=%s miktar=%s toplam=%s TL",
        siparis_id,
        urun_id,
        miktar,
        toplam_float,
    )
    return {
        "siparis_id": siparis_id,
        "urun_adi": ad,
        "miktar": miktar,
        "toplam_tutar": toplam_float,
        "mock_odeme_url": mock_url,
        "musteri_adi": musteri_adi,
    }


async def create_order(
    urun_id: int,
    miktar: int,
    musteri_adi: str,
    adres: str,
    kooperatif_id: int | None = None,
) -> dict[str, Any]:
    """Sipariş + kalem + stok düşümü; atomik transaction."""
    try:
        return await asyncio.to_thread(
            _create_order_transaction,
            urun_id,
            miktar,
            musteri_adi,
            adres,
            1,
            kooperatif_id,
        )
    except Exception:
        logger.exception("create_order: beklenmeyen veritabanı hatası")
        return {"error": "Sipariş kaydedilirken bir hata oluştu. Lütfen tekrar deneyin."}
