"""
Semantik arama araçları — ChromaRetriever üzerinden (chromadb ilk çağrıda yüklenir).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rag.retriever import ChromaRetriever

_retriever: Any = None


def get_retriever() -> Any:
    """chromadb / rag yalnızca ilk semantik çağrıda import edilir (uvicorn import zinciri kırılır)."""
    global _retriever
    if _retriever is None:
        try:
            from rag.retriever import ChromaRetriever

            _retriever = ChromaRetriever()
        except ImportError as e:
            logger.warning("chromadb veya bağımlılık yok: %s", e)
            raise RuntimeError(
                "Semantik arama için chromadb gerekli. Proje kökündeki venv'i kullanın: "
                "source ../venv/bin/activate && pip install -r requirements.txt"
            ) from e
        except Exception:
            logger.exception("ChromaRetriever oluşturulamadı")
            raise
    return _retriever


def _rag_unavailable_payload(exc: BaseException) -> list[dict[str, Any]]:
    return [
        {
            "hata": "semantic_search_kullanilamiyor",
            "mesaj": str(exc),
            "cozum": "Backend dizininde aktif venv ile: pip install -r requirements.txt",
        }
    ]


async def semantic_search_urunler(
    query: str,
    kategori: str | None = None,
    bolge: str | None = None,
    min_fiyat: float | None = None,
    max_fiyat: float | None = None,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    try:
        retriever = get_retriever()
        return await retriever.search_urunler(
            query=query,
            n_results=5,
            kategori=kategori,
            bolge=bolge,
            min_fiyat=min_fiyat,
            max_fiyat=max_fiyat,
            kooperatif_id=kooperatif_id,
        )
    except (ImportError, RuntimeError, ModuleNotFoundError) as e:
        logger.warning("semantic_search_urunler: RAG kullanılamıyor: %s", e)
        return _rag_unavailable_payload(e)
    except Exception:
        logger.exception("semantic_search_urunler")
        return []


async def semantic_search_kooperatifler(query: str) -> list[dict[str, Any]]:
    try:
        retriever = get_retriever()
        return await retriever.search_kooperatifler(query, n_results=3)
    except (ImportError, RuntimeError, ModuleNotFoundError) as e:
        logger.warning("semantic_search_kooperatifler: RAG kullanılamıyor: %s", e)
        return _rag_unavailable_payload(e)
    except Exception:
        logger.exception("semantic_search_kooperatifler")
        return []
