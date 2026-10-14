"""One-shot pairing-code store (M17 网关配对码).

In-memory store for short-lived, single-use pairing codes that bind a fresh
gateway install to this backend (PRD: 扫码/输入配对码与 Web 服务绑定).

Why not Redis: the backend deploys as a single uvicorn process and a code is
worthless after its 10-minute window (regenerating costs one click), so a
process-local dict gives identical semantics with zero infrastructure
dependency.  Codes are 8 chars from a 32-symbol alphabet (~1e12 space) —
brute force is impractical within the TTL, and the pair endpoint is
rate-limited on top.

Security properties:
  * one-shot — consuming a code deletes it (atomic under the store lock);
  * TTL 10 minutes, expired codes are pruned lazily on access;
  * scoping — a code is bound to the admin user who created it.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

CODE_TTL_SECONDS = 600  # 10 minutes — enough to install, short enough to leak harmlessly
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/1/0/O — transcription-safe


@dataclass(frozen=True)
class PairCode:
    code: str
    created_by: str  # admin user id (uuid str)
    expires_at: datetime


_store: dict[str, tuple[PairCode, float]] = {}  # code → (payload, expire_ts)
_lock = asyncio.Lock()

# Per-IP rate limit shared by the unauthenticated /pair endpoints (gateway M17
# + EA bridge P4): anti brute-force on top of the ~1e12 code space + 10min TTL.
_attempts: dict[str, list[float]] = {}


def rate_limited(ip: str) -> bool:
    import time

    now = time.monotonic()
    window = [t for t in _attempts.get(ip, []) if now - t < 60.0]
    if len(window) >= 10:
        _attempts[ip] = window
        return True
    window.append(now)
    _attempts[ip] = window
    return False


def reset_attempts() -> None:
    """Clear the per-IP attempt window (tests)."""
    _attempts.clear()


def new_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _prune(now: float) -> None:
    for k in [k for k, (_, exp) in _store.items() if exp <= now]:
        del _store[k]


async def create(created_by: str) -> PairCode:
    """Mint a fresh one-shot code."""
    async with _lock:
        _prune(time.monotonic())
        payload = PairCode(
            code=new_code(), created_by=created_by,
            expires_at=datetime.now(UTC) + timedelta(seconds=CODE_TTL_SECONDS),
        )
        _store[payload.code] = (payload, time.monotonic() + CODE_TTL_SECONDS)
        return payload


async def consume(code: str) -> PairCode | None:
    """Validate + delete atomically; None when unknown/expired.

    Accepts the raw code or the common ``XXXX-XXXX`` display form.
    """
    normalized = code.replace("-", "").replace(" ", "").upper()
    async with _lock:
        _prune(time.monotonic())
        entry = _store.pop(normalized, None)
        return entry[0] if entry is not None else None


def reset() -> None:
    """Clear all codes (tests)."""
    _store.clear()
