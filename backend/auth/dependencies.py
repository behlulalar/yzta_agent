"""FastAPI bağımlılıkları — Bearer JWT."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Header, HTTPException

from auth.jwt_handler import decode_token


async def get_current_kooperatif(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token gerekli")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token gerekli")
    return decode_token(token)
