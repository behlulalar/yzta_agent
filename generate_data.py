#!/usr/bin/env python3
"""
Kök dizin uyumluluğu: gerçek betik backend/generate_data.py içindedir.

Çalıştırma: python generate_data.py  veya  python backend/generate_data.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    script = Path(__file__).resolve().parent / "backend" / "generate_data.py"
    if not script.is_file():
        sys.stderr.write(f"Beklenen dosya bulunamadı: {script}\n")
        raise SystemExit(1)
    raise SystemExit(subprocess.call([sys.executable, str(script)] + sys.argv[1:]))


if __name__ == "__main__":
    main()
