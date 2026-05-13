#!/usr/bin/env python3
"""
PostgreSQL ürünleri + kooperatif hikâyeleri JSON'unu ChromaDB'ye indexler.

Çalıştırma (backend dizininden):
  python rag/indexer.py              # İnteraktif: önce temizleme sorulur
  python rag/indexer.py --fresh      # Koleksiyonları sil, sıfırdan indexle (önerilen)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import chromadb
from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

load_dotenv(_BACKEND_ROOT / ".env")

from database import close_pool, fetch_all, init_pool  # noqa: E402
from rag.embedder import ChromaEmbedder, DEFAULT_CHROMA_PATH  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rag.indexer")

HIKAYE_JSON = _BACKEND_ROOT / "data" / "kooperatif_hikayeleri.json"


async def index_urunler(embedder: ChromaEmbedder) -> int:
    sql = """
        SELECT u.id, u.ad, u.kategori, u.birim, u.fiyat, u.stok,
               u.aciklama, u.kooperatif_id,
               k.ad AS kooperatif_ad, k.bolge
        FROM urunler u
        JOIN kooperatifler k ON k.id = u.kooperatif_id
        ORDER BY u.id
    """
    try:
        rows = await fetch_all(sql)
    except Exception:
        logger.exception("Ürünler PostgreSQL'den okunamadı")
        raise

    n = 0
    for i, row in enumerate(rows):
        try:
            await embedder.add_urun(dict(row))
            n += 1
            if (i + 1) % 10 == 0:
                logger.info("Ürün indexlendi: %s / %s", i + 1, len(rows))
            await asyncio.sleep(0.1)
        except Exception:
            logger.exception("Ürün satırı atlandı: id=%s", row.get("id"))

    return n


async def index_kooperatifler(embedder: ChromaEmbedder) -> int:
    try:
        raw = HIKAYE_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        logger.exception("JSON okunamadı: %s", HIKAYE_JSON)
        raise

    if not isinstance(data, list):
        raise ValueError("kooperatif_hikayeleri.json bir liste olmalı")

    n = 0
    for i, koop in enumerate(data):
        try:
            await embedder.add_kooperatif(dict(koop))
            n += 1
            if (i + 1) % 5 == 0:
                logger.info("Kooperatif indexlendi: %s / %s", i + 1, len(data))
            await asyncio.sleep(0.1)
        except Exception:
            logger.exception("Kooperatif atlandı: %s", koop.get("kooperatif_id"))

    return n


def wipe_chroma_collections(persist_path: Path) -> None:
    """urunler ve kooperatifler koleksiyonlarını kalıcı diskten kaldırır."""
    client = chromadb.PersistentClient(path=str(persist_path))
    for name in ("urunler", "kooperatifler"):
        try:
            client.delete_collection(name)
            logger.info("Koleksiyon silindi: %s", name)
        except Exception:
            logger.warning("Koleksiyon silinemedi (yok olabilir): %s", name)


def _maybe_wipe_chroma(persist_path: Path, *, fresh: bool) -> None:
    if fresh:
        logger.info("--fresh: Chroma koleksiyonları temizleniyor...")
        wipe_chroma_collections(persist_path)
        return

    try:
        ans = input("Mevcut index temizlensin mi? (e/h): ").strip().lower()
    except EOFError:
        ans = "h"
    if ans != "e":
        logger.info("Mevcut koleksiyonlar korunuyor (incremental upsert).")
        return

    wipe_chroma_collections(persist_path)


async def _async_main(*, fresh: bool) -> None:
    persist_path = DEFAULT_CHROMA_PATH
    persist_path.mkdir(parents=True, exist_ok=True)

    _maybe_wipe_chroma(persist_path, fresh=fresh)

    try:
        init_pool()
    except Exception:
        logger.exception("DB pool başlatılamadı")
        raise

    embedder = ChromaEmbedder(persist_directory=persist_path)

    try:
        nu = await index_urunler(embedder)
        nk = await index_kooperatifler(embedder)
        logger.info("Özet: %s ürün, %s kooperatif eklendi/güncellendi.", nu, nk)
    finally:
        close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description="ChromaDB RAG index (ürün + kooperatif).")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Mevcut urunler/kooperatifler koleksiyonlarını sil; sıfırdan tam yeniden indexle.",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(fresh=args.fresh))


if __name__ == "__main__":
    main()
