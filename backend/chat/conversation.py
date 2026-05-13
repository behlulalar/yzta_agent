"""Çok turnlu sohbet: normalize etme, kırpma, son kullanıcı mesajı."""

from __future__ import annotations

MAX_CHAT_TURNS = 28


def normalize_chat_turns(turns: str | list[dict[str, str]]) -> list[dict[str, str]]:
    if isinstance(turns, str):
        c = turns.strip()
        return [{"role": "user", "content": c}] if c else []

    out: list[dict[str, str]] = []
    for t in turns:
        role = t.get("role")
        content = (t.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append({"role": role, "content": content})
    return out


def trim_turns(turns: list[dict[str, str]], max_turns: int = MAX_CHAT_TURNS) -> list[dict[str, str]]:
    if len(turns) <= max_turns:
        return turns
    return turns[-max_turns:]


def last_user_message(turns: list[dict[str, str]]) -> str:
    for t in reversed(turns):
        if t.get("role") == "user":
            return t.get("content") or ""
    return ""
