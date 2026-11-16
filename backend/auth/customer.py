"""Customer account authentication helpers."""
from __future__ import annotations

import hmac
import time
from typing import Optional

import jwt

ISSUER = "dropship-mvp"
CUSTOMER_AUDIENCE = "customer"


def issue_customer_token(secret: str, customer_id: int, email: str, ttl_minutes: int = 10080) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": str(customer_id), "email": email, "iat": now,
         "exp": now + ttl_minutes * 60, "iss": ISSUER, "aud": CUSTOMER_AUDIENCE},
        secret, algorithm="HS256",
    )


def verify_customer_token(secret: str, token: str) -> Optional[dict]:
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"], audience=CUSTOMER_AUDIENCE, issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        return {"id": int(claims["sub"]), "email": claims.get("email", "")}
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def customer_token_from_request(request, secret: str) -> Optional[dict]:
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer "):
        return None
    return verify_customer_token(secret, value[7:].strip())


def same_email(a: str, b: str) -> bool:
    return hmac.compare_digest(a.strip().lower(), b.strip().lower())
