"""
ChromaDB üzerinden semantik sorgu.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, cast

from chromadb.api.types import QueryResult
from rag.embedder import ChromaEmbedder, DEFAULT_CHROMA_PATH

logger = logging.getLogger(__name__)


class ChromaRetriever:
    def __init__(self, embedder: ChromaEmbedder | None = None) -> None:
        try:
            self.embedder = embedder or ChromaEmbedder(
                persist_directory=os.getenv("CHROMA_PERSIST_DIR", str(DEFAULT_CHROMA_PATH)),
            )
            self._urunler = self.embedder.urunler_collection
            self._kooperatifler = self.embedder.kooperatifler_collection
        except Exception:
            logger.exception("ChromaRetriever başlatılamadı")
            raise

    def _build_where(
        self,
        *,
        min_stok: int,
        kategori: str | None,
        bolge: str | None,
        min_fiyat: float | None,
        max_fiyat: float | None,
        kooperatif_id: int | None = None,
    ) -> dict[str, Any] | None:
        clauses: list[dict[str, Any]] = [{"stok": {"$gte": min_stok}}]

        if kooperatif_id is not None:
            clauses.append({"kooperatif_id": {"$eq": int(kooperatif_id)}})

        if kategori and str(kategori).strip():
            clauses.append({"kategori": {"$eq": str(kategori).strip()}})
        if bolge and str(bolge).strip():
            clauses.append({"bolge": {"$eq": str(bolge).strip()}})
        if min_fiyat is not None:
            clauses.append({"fiyat": {"$gte": float(min_fiyat)}})
        if max_fiyat is not None:
            clauses.append({"fiyat": {"$lte": float(max_fiyat)}})

        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    async def search_urunler(
        self,
        query: str,
        n_results: int = 5,
        kategori: str | None = None,
        bolge: str | None = None,
        min_fiyat: float | None = None,
        max_fiyat: float | None = None,
        min_stok: int = 1,
        kooperatif_id: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            q = (query or "").strip()
            if not q:
                return []

            where = self._build_where(
                min_stok=min_stok,
                kategori=kategori,
                bolge=bolge,
                min_fiyat=min_fiyat,
                max_fiyat=max_fiyat,
                kooperatif_id=kooperatif_id,
            )

            query_embedding = await self.embedder.embed_text(q)

            def _sync_query() -> QueryResult:
                return self._urunler.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    where=where,
                    include=["metadatas", "distances"],
                )

            raw = await asyncio.to_thread(_sync_query)
            metas = (raw.get("metadatas") or [[]])[0]
            dists = (raw.get("distances") or [[]])[0]

            out: list[dict[str, Any]] = []
            for md, dist in zip(metas, dists):
                if not md:
                    continue
                md_row = cast(dict[str, Any], md)
                stok = int(md_row.get("stok") or 0)
                if stok <= 0:
                    continue
                try:
                    d = float(dist)
                    benzerlik = max(0.0, min(1.0, 1.0 - d))
                except (TypeError, ValueError):
                    benzerlik = 0.0

                out.append(
                    {
                        "urun_id": md_row.get("urun_id"),
                        "ad": md_row.get("ad"),
                        "kategori": md_row.get("kategori"),
                        "bolge": md_row.get("bolge"),
                        "fiyat": md_row.get("fiyat"),
                        "stok": md_row.get("stok"),
                        "kooperatif_ad": md_row.get("kooperatif_ad"),
                        "kooperatif_id": md_row.get("kooperatif_id"),
                        "birim": md_row.get("birim"),
                        "benzerlik_skoru": round(benzerlik, 4),
                    }
                )

            return out
        except Exception:
            logger.exception("search_urunler başarısız")
            raise

    async def search_kooperatifler(self, query: str, n_results: int = 3) -> list[dict[str, Any]]:
        try:
            q = (query or "").strip()
            if not q:
                return []

            query_embedding = await self.embedder.embed_text(q)

            def _sync_query() -> QueryResult:
                return self._kooperatifler.query(
                    query_embeddings=[query_embedding],
                    n_results=n_results,
                    include=["metadatas", "distances"],
                )

            raw = await asyncio.to_thread(_sync_query)
            metas = (raw.get("metadatas") or [[]])[0]
            dists = (raw.get("distances") or [[]])[0]

            out: list[dict[str, Any]] = []
            for md, dist in zip(metas, dists):
                if not md:
                    continue
                try:
                    d = float(dist)
                    benzerlik = max(0.0, min(1.0, 1.0 - d))
                except (TypeError, ValueError):
                    benzerlik = 0.0

                row = dict(cast(dict[str, Any], md))
                row["benzerlik_skoru"] = round(benzerlik, 4)
                out.append(row)

            return out
        except Exception:
            logger.exception("search_kooperatifler başarısız")
            raise
