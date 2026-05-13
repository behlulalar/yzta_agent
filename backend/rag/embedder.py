"""
OpenAI embedding + ChromaDB kalıcı koleksiyonları.
"""

from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHROMA_PATH = _BACKEND_ROOT / "chroma_db"

EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def _float_or_zero(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _int_or_zero(v: Any) -> int:
    if v is None:
        return 0
    return int(v)


def _flatten_koop_metadata(koop: dict[str, Any]) -> dict[str, Any]:
    """Chroma metadata: düz str/int/float/bool."""
    meta: dict[str, Any] = {}
    for k, v in koop.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            meta[k] = v
        elif isinstance(v, Decimal):
            meta[k] = float(v)
        else:
            meta[str(k)] = str(v)
    return meta


class ChromaEmbedder:
    """Ürün ve kooperatif vektörlerini ChromaDB'de yönetir."""

    def __init__(
        self,
        persist_directory: str | Path | None = None,
        chroma_client: ClientAPI | None = None,
        openai_client: AsyncOpenAI | None = None,
    ) -> None:
        path = Path(persist_directory or os.getenv("CHROMA_PERSIST_DIR", str(DEFAULT_CHROMA_PATH)))
        path.mkdir(parents=True, exist_ok=True)

        self._async_openai = openai_client
        self._persist_path = path

        try:
            self._chroma = chroma_client or chromadb.PersistentClient(path=str(path))
            self._col_urunler: Collection = self._chroma.get_or_create_collection(
                name="urunler",
                metadata={"hnsw:space": "cosine"},
            )
            self._col_kooperatifler: Collection = self._chroma.get_or_create_collection(
                name="kooperatifler",
                metadata={"hnsw:space": "cosine"},
            )
        except Exception:
            logger.exception("ChromaDB başlatılamadı: %s", path)
            raise

    @property
    def urunler_collection(self) -> Collection:
        return self._col_urunler

    @property
    def kooperatifler_collection(self) -> Collection:
        return self._col_kooperatifler

    def _get_openai(self) -> AsyncOpenAI:
        if self._async_openai is None:
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY gerekli (embeddings için).")
            self._async_openai = AsyncOpenAI(api_key=key)
        return self._async_openai

    async def embed_text(self, text: str) -> list[float]:
        try:
            client = self._get_openai()
            resp = await client.embeddings.create(model=EMBEDDING_MODEL, input=text[:8000])
            return list(resp.data[0].embedding)
        except Exception:
            logger.exception("embed_text başarısız")
            raise

    async def add_urun(self, urun: dict[str, Any]) -> None:
        try:
            uid = urun.get("id") or urun.get("urun_id")
            if uid is None:
                raise ValueError("urun id yok")

            ad = str(urun.get("ad") or "")
            aciklama = str(urun.get("aciklama") or "")
            kategori = str(urun.get("kategori") or "")
            bolge = str(urun.get("bolge") or "")
            koop_ad = str(urun.get("kooperatif_ad") or "")
            doc = f"{ad}. {aciklama}. Kategori: {kategori}. Bölge: {bolge}. Kooperatif: {koop_ad}."

            embedding = await self.embed_text(doc)

            meta = {
                "urun_id": _int_or_zero(uid),
                "ad": ad[:500],
                "kategori": kategori[:200],
                "bolge": bolge[:200],
                "fiyat": _float_or_zero(urun.get("fiyat")),
                "stok": _int_or_zero(urun.get("stok")),
                "kooperatif_id": _int_or_zero(urun.get("kooperatif_id")),
                "kooperatif_ad": koop_ad[:500],
                "birim": str(urun.get("birim") or "")[:80],
            }

            def _sync_add() -> None:
                self._col_urunler.upsert(
                    ids=[str(uid)],
                    embeddings=[embedding],
                    metadatas=[meta],
                    documents=[doc],
                )

            await asyncio.to_thread(_sync_add)
        except Exception:
            logger.exception("add_urun başarısız: %s", urun.get("id"))
            raise

    async def add_kooperatif(self, koop: dict[str, Any]) -> None:
        try:
            kid = koop.get("kooperatif_id")
            if kid is None:
                raise ValueError("kooperatif_id yok")

            ad = str(koop.get("ad") or "")
            hikaye = str(koop.get("hikaye") or "")
            ozellik = str(koop.get("urun_ozellikleri") or "")
            bolge = str(koop.get("bolge") or "")
            doc = f"{ad}. {hikaye}. {ozellik}. Bölge: {bolge}."

            embedding = await self.embed_text(doc)
            meta = _flatten_koop_metadata(koop)

            def _sync_add() -> None:
                self._col_kooperatifler.upsert(
                    ids=[str(kid)],
                    embeddings=[embedding],
                    metadatas=[meta],
                    documents=[doc],
                )

            await asyncio.to_thread(_sync_add)
        except Exception:
            logger.exception("add_kooperatif başarısız: %s", koop.get("kooperatif_id"))
            raise

    async def update_urun_stok(self, urun_id: int, yeni_stok: int) -> None:
        try:
            sid = str(urun_id)

            def _sync_update() -> None:
                self._col_urunler.update(ids=[sid], metadatas=[{"stok": int(yeni_stok)}])

            await asyncio.to_thread(_sync_update)
        except Exception:
            logger.exception("update_urun_stok başarısız: urun_id=%s", urun_id)
