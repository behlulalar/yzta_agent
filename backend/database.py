"""
PostgreSQL bağlantı yönetimi.
psycopg2 senkron bir kütüphane olduğu için tüm DB çağrıları
asyncio.to_thread ile thread pool'a devredilir; böylece FastAPI
endpoint'leri async olarak çalışmaya devam eder.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any, Iterable

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "kooperatif_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

MIN_CONN = int(os.getenv("DB_POOL_MIN", "1"))
MAX_CONN = int(os.getenv("DB_POOL_MAX", "10"))

_pool: pool.ThreadedConnectionPool | None = None


def init_pool() -> None:
    """Uygulama başlangıcında bir kez çağrılır."""
    global _pool
    if _pool is not None:
        return
    _pool = pool.ThreadedConnectionPool(
        minconn=MIN_CONN,
        maxconn=MAX_CONN,
        **DB_CONFIG,
    )


def close_pool() -> None:
    """Uygulama kapanırken çağrılır."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn():
    """
    Pool'dan bir bağlantı alır, kullanım sonrası otomatik geri verir.
    Hata durumunda rollback uygular.
    """
    if _pool is None:
        raise RuntimeError("DB pool başlatılmadı. Önce init_pool() çağırın.")

    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _run_query(
    sql: str,
    params: Iterable[Any] | None,
    fetch: str | None,
) -> Any:
    """
    Senkron sorgu yürütücü. fetch:
      - "all" → list[dict]
      - "one" → dict | None
      - None  → None (INSERT/UPDATE/DELETE için)
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]
            if fetch == "one":
                row = cur.fetchone()
                return dict(row) if row else None
            return None


async def fetch_all(sql: str, params: Iterable[Any] | None = None) -> list[dict]:
    return await asyncio.to_thread(_run_query, sql, params, "all")


async def fetch_one(sql: str, params: Iterable[Any] | None = None) -> dict | None:
    return await asyncio.to_thread(_run_query, sql, params, "one")


async def execute(sql: str, params: Iterable[Any] | None = None) -> None:
    await asyncio.to_thread(_run_query, sql, params, None)


async def healthcheck() -> bool:
    """Bağlantı sağlık kontrolü; FastAPI startup için kullanışlı."""
    try:
        result = await fetch_one("SELECT 1 AS ok;")
        return bool(result and result.get("ok") == 1)
    except Exception:
        return False
