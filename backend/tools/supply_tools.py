"""
Proaktif Tedarik Ajanı için SQL araçları.
Tüm çağrılar async; database.py'daki fetch_all / fetch_one / execute kullanır.
Sayısal hesaplar burada yapılır; LLM'e ham sayı verilmez.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import database

logger = logging.getLogger(__name__)

SENT_MAILS_DIR = os.getenv("SENT_MAILS_DIR", "sent_mails")


# ── STOK ANALİZİ ─────────────────────────────────────────────────────────────

async def get_dusuk_stok_urunler(
    kooperatif_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    stok <= kritik_esik olan ürünleri döner.
    Opsiyonel olarak kooperatif filtresi uygulanır.
    """
    if kooperatif_id:
        rows = await database.fetch_all(
            """
            SELECT
                u.id, u.ad, u.kategori, u.fiyat, u.stok, u.kritik_esik,
                u.lead_time_gun, u.kooperatif_id,
                k.ad  AS kooperatif_adi,
                k.email AS kooperatif_email,
                k.bolge
            FROM urunler u
            JOIN kooperatifler k ON k.id = u.kooperatif_id
            WHERE u.stok <= u.kritik_esik
              AND u.kooperatif_id = %s
            ORDER BY u.stok ASC
            LIMIT %s
            """,
            (kooperatif_id, limit),
        )
    else:
        rows = await database.fetch_all(
            """
            SELECT
                u.id, u.ad, u.kategori, u.fiyat, u.stok, u.kritik_esik,
                u.lead_time_gun, u.kooperatif_id,
                k.ad  AS kooperatif_adi,
                k.email AS kooperatif_email,
                k.bolge
            FROM urunler u
            JOIN kooperatifler k ON k.id = u.kooperatif_id
            WHERE u.stok <= u.kritik_esik
            ORDER BY u.stok ASC
            LIMIT %s
            """,
            (limit,),
        )
    return rows or []


async def get_satis_hizi(urun_id: int, gun: int = 30) -> float:
    """
    Son N günde günlük ortalama satış adedi.
    Hesap SQL'de yapılır; LLM'e bırakılmaz.
    """
    row = await database.fetch_one(
        """
        SELECT COALESCE(SUM(ABS(miktar)), 0)::float AS toplam_satis
        FROM stok_hareketleri
        WHERE urun_id = %s
          AND hareket_turu = 'satis'
          AND olusturulma >= NOW() - (%s::integer * INTERVAL '1 day')
        """,
        (urun_id, gun),
    )
    toplam = float((row or {}).get("toplam_satis", 0))
    return round(toplam / gun, 2) if gun > 0 else 0.0


async def get_urun(urun_id: int) -> dict | None:
    """Tek ürün detayı (kooperatif bilgisiyle)."""
    return await database.fetch_one(
        """
        SELECT
            u.id, u.ad, u.kategori, u.birim, u.fiyat, u.stok, u.kritik_esik,
            u.lead_time_gun, u.kooperatif_id,
            k.ad    AS kooperatif_adi,
            k.email AS kooperatif_email,
            k.bolge
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.id = %s
        """,
        (urun_id,),
    )


async def get_kooperatif_transferi(
    urun_adi: str,
    hariç_kooperatif_id: int,
) -> list[dict]:
    """
    Dayanışma özelliği: aynı ürün adına sahip, fazla stoğu olan
    başka kooperatifleri döner.
    'Fazla stok' = stok > kritik_esik * 3
    """
    rows = await database.fetch_all(
        """
        SELECT
            u.id, u.ad, u.stok, u.kritik_esik,
            u.kooperatif_id,
            k.ad    AS kooperatif_adi,
            k.bolge,
            k.email AS kooperatif_email
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        WHERE u.ad ILIKE %s
          AND u.kooperatif_id != %s
          AND u.stok > u.kritik_esik * 3
        ORDER BY u.stok DESC
        LIMIT 3
        """,
        (f"%{urun_adi}%", hariç_kooperatif_id),
    )
    return rows or []


# ── ANALİZ (Python hesapları) ─────────────────────────────────────────────────

async def urun_analiz_et(urun: dict) -> dict:
    """
    Bir ürün için kritiklik analizi üretir.
    Tüm matematik burada; LLM'e sayı hesapletilmez.
    """
    urun_id   = urun["id"]
    stok      = int(urun["stok"])
    lead_time = int(urun.get("lead_time_gun") or 7)

    hiz = await get_satis_hizi(urun_id, gun=30)

    # Stok kaç gün yeter?
    kalan_gun = round(stok / hiz, 1) if hiz > 0 else 999.0

    # Önerilen sipariş: 30 günlük talep + %20 güvenlik payı, en az 10
    onerilen_miktar = max(int(hiz * 30 * 1.2), 10)

    # Kritiklik: lead_time'a göre dinamik
    if kalan_gun < lead_time:
        kritiklik = "critical"   # tedarik gelmeden stok biter
    elif kalan_gun < lead_time * 1.5:
        kritiklik = "warning"
    else:
        kritiklik = "info"

    # Kooperatif dayanışması: başka kooperatifte fazla stok var mı?
    transfer_secenekleri = await get_kooperatif_transferi(
        urun_adi=str(urun["ad"]),
        hariç_kooperatif_id=int(urun["kooperatif_id"]),
    )

    return {
        "urun_id":              urun_id,
        "urun_adi":             urun["ad"],
        "kooperatif_id":        urun["kooperatif_id"],
        "kooperatif_adi":       urun.get("kooperatif_adi"),
        "kooperatif_email":     urun.get("kooperatif_email"),
        "bolge":                urun.get("bolge"),
        "mevcut_stok":          stok,
        "kritik_esik":          int(urun["kritik_esik"]),
        "gunluk_satis_hizi":    hiz,
        "kalan_gun":            kalan_gun,
        "lead_time_gun":        lead_time,
        "onerilen_miktar":      onerilen_miktar,
        "kritiklik":            kritiklik,
        "transfer_secenekleri": transfer_secenekleri,
    }


# ── BİLDİRİM CRUD ─────────────────────────────────────────────────────────────

async def bildirim_kaydet(
    tip: str,
    urun_id: int,
    kooperatif_id: int,
    baslik: str,
    govde: str,
    kritiklik: str,
    veri: dict,
) -> int:
    """Yeni bildirim ekler; eklenen kaydın id'sini döner."""
    row = await database.fetch_one(
        """
        INSERT INTO bildirimler
            (tip, urun_id, kooperatif_id, baslik, govde, kritiklik, veri)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (tip, urun_id, kooperatif_id, baslik, govde, kritiklik,
         json.dumps(veri, ensure_ascii=False)),
    )
    return int((row or {}).get("id", -1))


async def get_bildirimler(okunmamis_sadece: bool = True) -> list[dict]:
    """Frontend polling için okunmamış bildirimleri döner."""
    if okunmamis_sadece:
        rows = await database.fetch_all(
            """
            SELECT b.*, k.ad AS kooperatif_adi, u.ad AS urun_adi
            FROM bildirimler b
            LEFT JOIN kooperatifler k ON k.id = b.kooperatif_id
            LEFT JOIN urunler       u ON u.id = b.urun_id
            WHERE b.okundu = FALSE
            ORDER BY b.olusturulma DESC
            LIMIT 50
            """,
        )
    else:
        rows = await database.fetch_all(
            """
            SELECT b.*, k.ad AS kooperatif_adi, u.ad AS urun_adi
            FROM bildirimler b
            LEFT JOIN kooperatifler k ON k.id = b.kooperatif_id
            LEFT JOIN urunler       u ON u.id = b.urun_id
            ORDER BY b.olusturulma DESC
            LIMIT 50
            """,
        )
    return rows or []


async def bildirim_aksiyon_al(bildirim_id: int, mail_dosya_yolu: str | None = None) -> None:
    """Onayla butonuna basılınca: aksiyon_alindi=True, okundu=True."""
    await database.execute(
        """
        UPDATE bildirimler
        SET aksiyon_alindi = TRUE,
            okundu         = TRUE,
            mail_dosya_yolu = COALESCE(%s, mail_dosya_yolu)
        WHERE id = %s
        """,
        (mail_dosya_yolu, bildirim_id),
    )


# ── MOCK MAİL DOSYAYA YAZ ─────────────────────────────────────────────────────

def mock_mail_kaydet(urun_adi: str, mail_konu: str, mail_govde: str) -> str:
    """
    Mock mail'i sent_mails/ klasörüne .txt olarak kaydeder.
    Dosya yolunu döner (bildirimler.mail_dosya_yolu için).
    """
    os.makedirs(SENT_MAILS_DIR, exist_ok=True)
    zaman_damgasi = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya_adi = f"{zaman_damgasi}_{urun_adi.replace(' ', '_')[:30]}.txt"
    tam_yol = os.path.join(SENT_MAILS_DIR, dosya_adi)

    icerik = f"KONU: {mail_konu}\n{'─' * 50}\n{mail_govde}\n"
    with open(tam_yol, "w", encoding="utf-8") as f:
        f.write(icerik)

    logger.info("Mock mail kaydedildi: %s", tam_yol)
    return tam_yol
