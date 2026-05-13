"""
Proaktif stok tarayıcı — APScheduler ile gece 23:00'te çalışır.
Demo için: SUPPLY_CHECK_INTERVAL_MIN=2 env değişkeniyle 2 dakikada bir tetiklenebilir.

FastAPI lifespan'ına main.py'da bağlanır.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from tools.supply_tools import (
    get_dusuk_stok_urunler,
    urun_analiz_et,
    bildirim_kaydet,
    mock_mail_kaydet,
)

# supply_agent içindeki mail üretici fonksiyonu import ediyoruz
from agents.supply_agent import _mail_uret

logger = logging.getLogger(__name__)

# Demo için override: SUPPLY_CHECK_INTERVAL_MIN=2 → her 2 dakikada bir
DEMO_INTERVAL_MIN = int(os.getenv("SUPPLY_CHECK_INTERVAL_MIN", "0"))

scheduler = AsyncIOScheduler()


async def _proaktif_tarama() -> None:
    """
    Tüm kritik ürünleri tarar, her biri için:
    1. Satış hızı hesaplar
    2. Kritiklik belirler
    3. Mail taslağı üretir (LLM)
    4. bildirimler tablosuna kaydeder
    5. sent_mails/ klasörüne yazar
    """
    logger.info("🌙 Proaktif stok taraması başladı")
    try:
        kritik_urunler = await get_dusuk_stok_urunler(limit=50)
        if not kritik_urunler:
            logger.info("Proaktif tarama: kritik ürün yok")
            return

        logger.info("Proaktif tarama: %s kritik ürün bulundu", len(kritik_urunler))
        islem_sayisi = 0

        for urun in kritik_urunler:
            try:
                analiz = await urun_analiz_et(urun)

                # Sadece critical ve warning için aksiyon al
                if analiz["kritiklik"] == "info":
                    continue

                # Mail taslağı üret
                mail_metni = await _mail_uret(analiz)
                ilk_satir = mail_metni.split("\n")[0]
                konu = (
                    ilk_satir.replace("KONU:", "").strip()
                    if "KONU:" in ilk_satir
                    else f"{urun['ad']} - Tedarik Talebi"
                )

                # Dosyaya yaz
                dosya_yolu = mock_mail_kaydet(str(urun["ad"]), konu, mail_metni)

                # Transfer seçeneği varsa tip'i farklılaştır
                tip = (
                    "transfer_onerisi"
                    if analiz.get("transfer_secenekleri")
                    else "tedarik_onerisi"
                )

                baslik = (
                    f"{'🔴' if analiz['kritiklik'] == 'critical' else '🟡'} "
                    f"{urun['ad']} stoğu kritik "
                    f"({analiz['kalan_gun']} gün kaldı)"
                )

                await bildirim_kaydet(
                    tip=tip,
                    urun_id=analiz["urun_id"],
                    kooperatif_id=analiz["kooperatif_id"],
                    baslik=baslik,
                    govde=mail_metni,
                    kritiklik=analiz["kritiklik"],
                    veri={
                        "mevcut_stok":         analiz["mevcut_stok"],
                        "kritik_esik":         analiz["kritik_esik"],
                        "gunluk_satis_hizi":   analiz["gunluk_satis_hizi"],
                        "kalan_gun":           analiz["kalan_gun"],
                        "lead_time_gun":       analiz["lead_time_gun"],
                        "onerilen_miktar":     analiz["onerilen_miktar"],
                        "mail_konu":           konu,
                        "dosya_yolu":          dosya_yolu,
                        "transfer_secenekleri": analiz.get("transfer_secenekleri", []),
                    },
                )
                islem_sayisi += 1
                logger.info(
                    "Proaktif tarama: %s → %s",
                    urun["ad"],
                    analiz["kritiklik"],
                )

            except Exception:
                logger.exception("Proaktif tarama: ürün hatası (id=%s)", urun.get("id"))

        logger.info("✅ Proaktif tarama tamamlandı: %s bildirim üretildi", islem_sayisi)

    except Exception:
        logger.exception("Proaktif tarama: genel hata")


def start_scheduler() -> None:
    """main.py lifespan'ından çağrılır."""
    # Gece 23:00 production cron
    scheduler.add_job(
        _proaktif_tarama,
        trigger="cron",
        hour=23,
        minute=0,
        id="gece_tedarik_taramas",
        replace_existing=True,
    )

    # Demo modu: SUPPLY_CHECK_INTERVAL_MIN > 0 ise interval'e geç
    if DEMO_INTERVAL_MIN > 0:
        scheduler.add_job(
            _proaktif_tarama,
            trigger="interval",
            minutes=DEMO_INTERVAL_MIN,
            id="demo_tedarik_taramas",
            replace_existing=True,
        )
        logger.info(
            "Demo modu aktif: tedarik taraması her %s dakikada bir çalışacak",
            DEMO_INTERVAL_MIN,
        )

    scheduler.start()
    logger.info("📅 Tedarik scheduler başlatıldı")


def stop_scheduler() -> None:
    """main.py lifespan kapanışından çağrılır."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("📅 Tedarik scheduler durduruldu")
