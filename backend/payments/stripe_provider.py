"""Stripe Checkout (hosted page) provider - REFERENCE IMPLEMENTATION, UNTESTED LIVE.

Written against Stripe's public REST API with the standard library only. The webhook
signature scheme (`Stripe-Signature: t=...,v1=...`, HMAC-SHA256 over "t.payload") is
covered by unit tests; the HTTP calls to Stripe have NOT been exercised because no
Stripe credentials were available while building this MVP. Test with Stripe *test*
keys (PAYMENT_MODE=test, key starting sk_test_) before going live.

It can be swapped for any other provider by implementing `PaymentProvider`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping

from backend.payments.base import (
    CheckoutSession,
    InvalidWebhookSignature,
    PaymentError,
    PaymentEvent,
    PaymentProvider,
    PaymentStatus,
    RefundResult,
)

API = "https://api.stripe.com/v1"
TOLERANCE_SECONDS = 300


class StripePaymentProvider(PaymentProvider):
    name = "stripe"

    def __init__(self, secret_key: str, webhook_secret: str):
        self._key = secret_key
        self._whsec = webhook_secret

    # ------------------------------------------------------------------ http
    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
        req = urllib.request.Request(f"{API}{path}", data=body, method=method)
        req.add_header("Authorization", f"Bearer {self._key}")
        if body:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise PaymentError(f"Stripe error {exc.code}: {detail}")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PaymentError(f"Stripe unreachable: {exc}")

    # ------------------------------------------------------------------ interface
    def create_checkout_session(self, *, order_reference, amount_minor, currency,
                                customer_email, description, success_url, cancel_url):
        data = {
            "mode": "payment",
            "client_reference_id": order_reference,
            "customer_email": customer_email,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": amount_minor,
            "line_items[0][price_data][product_data][name]": description[:200],
            "metadata[order_reference]": order_reference,
        }
        s = self._request("POST", "/checkout/sessions", data)
        return CheckoutSession(s["id"], s["url"])

    def get_payment_status(self, reference: str) -> PaymentStatus:
        s = self._request("GET", f"/checkout/sessions/{urllib.parse.quote(reference)}")
        if s.get("payment_status") == "paid":
            return PaymentStatus("paid", s.get("amount_total"), (s.get("currency") or "").upper())
        if s.get("status") == "expired":
            return PaymentStatus("failed")
        return PaymentStatus("pending")

    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> PaymentEvent:
        self.verify_signature(headers.get("Stripe-Signature", ""), raw_body)
        try:
            event = json.loads(raw_body)
            obj = event["data"]["object"]
            etype = event["type"]
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidWebhookSignature(f"malformed event: {exc}")
        mapping = {
            "checkout.session.completed": "payment_succeeded" if obj.get("payment_status") == "paid" else "ignored",
            "checkout.session.async_payment_succeeded": "payment_succeeded",
            "checkout.session.async_payment_failed": "payment_failed",
            "checkout.session.expired": "payment_failed",
        }
        return PaymentEvent(
            event_id=event["id"],
            type=mapping.get(etype, "ignored"),
            reference=obj.get("id"),
            order_reference=obj.get("client_reference_id") or (obj.get("metadata") or {}).get("order_reference"),
            amount_minor=obj.get("amount_total"),
            currency=(obj.get("currency") or "").upper() or None,
        )

    def verify_signature(self, header: str, raw_body: bytes, now: float | None = None) -> None:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        ts, sigs = parts.get("t"), [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
        if not self._whsec or not ts or not sigs:
            raise InvalidWebhookSignature("missing signature")
        try:
            age = abs((now or time.time()) - int(ts))
        except ValueError:
            raise InvalidWebhookSignature("bad timestamp")
        if age > TOLERANCE_SECONDS:
            raise InvalidWebhookSignature("timestamp outside tolerance")
        expected = hmac.new(self._whsec.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, s) for s in sigs):
            raise InvalidWebhookSignature("signature mismatch")

    def refund(self, *, reference, amount_minor, currency, reason) -> RefundResult:
        s = self._request("GET", f"/checkout/sessions/{urllib.parse.quote(reference)}")
        pi = s.get("payment_intent")
        if not pi:
            raise PaymentError("No payment_intent on session; cannot refund")
        r = self._request("POST", "/refunds", {"payment_intent": pi, "amount": amount_minor})
        return RefundResult(r["id"], r.get("status", "unknown"))
