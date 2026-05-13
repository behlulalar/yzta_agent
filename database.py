"""
Repo kökünden `import database` yapıldığında backend/database modülünü yükler.

Önerilen çalışma dizini: backend/ (örn. uvicorn main:app).
"""

from __future__ import annotations

import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent / "backend"
if _backend_dir.is_dir():
    p = str(_backend_dir)
    if p not in sys.path:
        sys.path.insert(0, p)

from database import *  # noqa: F403, E402
