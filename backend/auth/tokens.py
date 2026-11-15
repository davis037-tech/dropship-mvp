"""Short-lived signed admin tokens (JWT, HS256) and customer order-status tokens."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

import jwt

ISSUER = "dropship-mvp"
AUDIENCE = "admin"


def issue_admin_token(secret: str, email: str, ttl_minutes: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": email, "iat": now, "exp": now + ttl_minutes * 60, "iss": ISSUER, "aud": AUDIENCE},
        secret, algorithm="HS256",
    )


def verify_admin_token(secret: str, token: str) -> Optional[str]:
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        return claims["sub"]
    except jwt.PyJWTError:
        return None


def order_access_token(secret: str, order_reference: str) -> str:
    """Unguessable token that lets the buyer open their own order page without an email."""
    return hmac.new(secret.encode(), f"order-status:{order_reference}".encode(), hashlib.sha256).hexdigest()[:32]
