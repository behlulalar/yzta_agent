#!/usr/bin/env python3
"""
Kooperatif giriş kolonlarını ekler ve her kooperatif için demo şifre hash'i yazar.

Çalıştırma (backend dizininden):
  python migration.py

Şifre kuralı: kooperatif adının ilk kelimesi + "2024" (örn. Zap Kadın Kooperatifi → Zap2024)
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

import database  # noqa: E402
from auth.password_hash import hash_password  # noqa: E402


def demo_plain_password(ad: str) -> str:
    parts = (ad or "").strip().split()
    if not parts:
        return "Kooperatif2024"
    w = parts[0]
    if not w:
        return "Kooperatif2024"
    cw = (w[0].upper() + w[1:]) if len(w) > 1 else w.upper()
    return f"{cw}2024"


def main() -> None:
    database.init_pool()
    print("migration: kolonlar kontrol ediliyor / ekleniyor...")
    with database.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE kooperatifler
                ADD COLUMN IF NOT EXISTS sifre_hash TEXT,
                ADD COLUMN IF NOT EXISTS aktif BOOLEAN DEFAULT TRUE;
                """
            )

    print("migration: kooperatif şifreleri üretiliyor (demo plaintext bu çıktıda)...\n")

    with database.get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, ad FROM kooperatifler ORDER BY id")
            rows = cur.fetchall()

        print("--- DEMO ŞİFRELER (üretimde kullanmayın) ---")
        for row in rows or []:
            kid = int(row["id"])
            ad = str(row["ad"] or "")
            plain = demo_plain_password(ad)
            h = hash_password(plain)
            print(f"  id={kid}  ad={ad!r}  demo_şifre={plain!r}")

            with conn.cursor() as cur2:
                cur2.execute(
                    "UPDATE kooperatifler SET sifre_hash = %s, aktif = COALESCE(aktif, TRUE) WHERE id = %s",
                    (h, kid),
                )

        print("--- bitti ---")

    database.close_pool()


if __name__ == "__main__":
    main()
