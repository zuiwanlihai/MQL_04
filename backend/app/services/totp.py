"""TOTP 2FA primitives (M9).

Thin wrapper over ``pyotp`` + ``qrcode``.  Secrets are returned to the caller
in plaintext only for the one-shot provisioning step; persistence is always
via :func:`app.services.credentials.encrypt_secret` (Fernet).  Recovery codes
are high-entropy one-time strings stored only as SHA-256 digests.

QR rendering uses qrcode's pure-Python **SVG** factory (no Pillow needed).
"""

from __future__ import annotations

import base64
import hashlib
import io
import secrets

import pyotp
import qrcode
from qrcode.image.svg import SvgPathImage


def new_secret() -> str:
    """Random Base32 TOTP secret (standard 20-byte entropy)."""
    return pyotp.random_base32()


def provisioning_uri(email: str, secret: str, issuer: str) -> str:
    """otpauth:// URI embedded in the QR code / shown for manual entry."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_code(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Verify a 6-digit TOTP with ±``valid_window`` step drift (default 1 → ±30s)."""
    if not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window)


def qr_svg_data_uri(uri: str) -> str:
    """Render ``uri`` as a QR SVG and return a ``data:image/svg+xml;base64,...``."""
    img = qrcode.make(uri, image_factory=SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def gen_recovery_codes(n: int = 10) -> list[str]:
    """Human-friendly one-time recovery codes (XXXX-XXXX, unambiguous alphabet)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
    out: list[str] = []
    for _ in range(n):
        block = "".join(secrets.choice(alphabet) for _ in range(8))
        out.append(f"{block[:4]}-{block[4:]}")
    return out


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def hash_recovery_codes(codes: list[str]) -> list[str]:
    return [hash_recovery_code(c) for c in codes]


def match_recovery_code(stored_hashes: list[str], code: str) -> int:
    """Return the index of the matching stored digest, or -1 when no match."""
    target = hash_recovery_code(code)
    try:
        return stored_hashes.index(target)
    except ValueError:
        return -1
