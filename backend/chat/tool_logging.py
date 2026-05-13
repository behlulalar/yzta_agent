"""Tool çıktılarının INFO düzeyinde özetlenmesi (tam kayıt değil, uzunluk / yapı)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_tool_result(name: str, result: Any) -> None:
    if isinstance(result, list):
        logger.info("Tool sonuç %s: liste uzunluğu=%s", name, len(result))
        return
    if isinstance(result, dict):
        logger.info("Tool sonuç %s: dict anahtarlar=%s", name, list(result.keys())[:15])
        return
    if result is None:
        logger.info("Tool sonuç %s: kayıt yok (None)", name)
        return
    logger.info("Tool sonuç %s: tip=%s", name, type(result).__name__)
