"""Rate limiting, CORS and security headers."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import Flask, current_app, request

from backend.api.errors import ApiError


class RateLimiter:
    """In-process sliding window. Fine for one worker; use Redis/edge limits when scaling out."""

    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = Lock()

    def allow(self, bucket: str, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 20000:  # opportunistic cleanup
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window]:
                    self._hits.pop(k, None)
            return True


def rate_limit(bucket: str, limit: int, window: int = 60):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            ctx = current_app.extensions["shop"]
            if ctx.settings.rate_limit_enabled and not ctx.limiter.allow(bucket, request.remote_addr or "?", limit, window):
                raise ApiError(429, "rate_limited", "Too many requests. Please slow down and try again shortly.")
            return fn(*a, **kw)
        return wrapper
    return deco


CSP = ("default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'")


def install(app: Flask, allowed_origins: tuple, production: bool) -> None:
    @app.before_request
    def _preflight():
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            from flask import make_response
            resp = make_response("", 204)
            return _cors(resp, allowed_origins)

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if resp.mimetype == "text/html":
            resp.headers.setdefault("Content-Security-Policy", CSP)
            resp.headers.setdefault("Cache-Control", "no-cache")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
            _cors(resp, allowed_origins)
        if production:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp


def _cors(resp, allowed_origins):
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") in allowed_origins:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "600"
    return resp
