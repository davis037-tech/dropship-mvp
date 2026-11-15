"""DEVELOPMENT ONLY sandbox payment provider (PAYMENT_PROVIDER=sandbox).

It simulates a hosted payment page served by our own backend at /dev/pay/<reference>.
Crucially, "paying" there does NOT mark anything paid directly: the page triggers a
*signed webhook* (HMAC-SHA256 with PAYMENT_WEBHOOK_SECRET) that goes through exactly the
same verification and idempotency path as a real provider. Disabled in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import uuid
from typing import Mapping

from backend.payments.base import (
    CheckoutSession,
    InvalidWebhookSignature,
    PaymentEvent,
    PaymentProvider,
    PaymentStatus,
    RefundResult,
)

SIGNATURE_HEADER = "X-Sandbox-Signature"


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_event_body(*, event_id=None, type_, reference, order_reference, amount_minor, currency) -> bytes:
    return json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex}",
            "type": type_,
            "reference": reference,
            "order_reference": order_reference,
            "amount": amount_minor,
            "currency": currency,
        },
        separators=(",", ":"),
    ).encode()


class SandboxPaymentProvider(PaymentProvider):
    name = "sandbox"

    def __init__(self, webhook_secret: str, base_url_getter):
        self._secret = webhook_secret
        self._base_url = base_url_getter
        self._sessions: dict = {}           # in-memory: development only
        self._lock = threading.Lock()

    def create_checkout_session(self, *, order_reference, amount_minor, currency,
                                customer_email, description, success_url, cancel_url):
        ref = "sbx_" + secrets.token_urlsafe(16)
        with self._lock:
            self._sessions[ref] = {
                "status": "pending", "amount": amount_minor, "currency": currency,
                "order_reference": order_reference, "success_url": success_url, "cancel_url": cancel_url,
            }
        return CheckoutSession(ref, f"{self._base_url()}/dev/pay/{ref}")

    def session(self, reference: str):
        with self._lock:
            return dict(self._sessions[reference]) if reference in self._sessions else None

    def set_status(self, reference: str, status: str) -> None:
        with self._lock:
            if reference in self._sessions:
                self._sessions[reference]["status"] = status

    def get_payment_status(self, reference: str) -> PaymentStatus:
        s = self.session(reference)
        if not s:
            return PaymentStatus("pending")
        return PaymentStatus(s["status"], s["amount"], s["currency"])

    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> PaymentEvent:
        given = headers.get(SIGNATURE_HEADER, "")
        if not self._secret or not hmac.compare_digest(given, sign(self._secret, raw_body)):
            raise InvalidWebhookSignature("bad signature")
        try:
            data = json.loads(raw_body)
            type_map = {"payment.succeeded": "payment_succeeded", "payment.failed": "payment_failed",
                        "payment.refunded": "refunded"}
            return PaymentEvent(
                event_id=str(data["id"]),
                type=type_map.get(data["type"], "ignored"),
                reference=data.get("reference"),
                order_reference=data.get("order_reference"),
                amount_minor=data.get("amount"),
                currency=data.get("currency"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidWebhookSignature(f"malformed event: {exc}")

    def refund(self, *, reference, amount_minor, currency, reason) -> RefundResult:
        self.set_status(reference, "refunded")
        return RefundResult("sbx_refund_" + secrets.token_hex(6), "succeeded")
