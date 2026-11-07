"""Browser origins allowed to call the API (03 §6.1 H8)."""

from __future__ import annotations

ALLOWED_ORIGINS = [
    "app://relay",
    "null",  # file:// pages send Origin: null
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]


def origin_allowed(origin: str) -> bool:
    return origin in ALLOWED_ORIGINS
