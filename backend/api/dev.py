"""Development-only routes for the sandbox payment provider.

Registered ONLY when PAYMENT_PROVIDER=sandbox and APP_ENV != production. The fake
"hosted payment page" does not mark anything paid itself: it posts a *signed webhook*
to the normal webhook processing path.
"""
from __future__ import annotations

import html

from flask import Blueprint, current_app, jsonify, make_response, redirect

from backend.api.errors import ApiError
from backend.models import orders as order_repo
from backend.payments import sandbox
from backend.payments.base import PaymentEvent
from backend.services import payment_processing

bp = Blueprint("dev", __name__)


def ctx():
    return current_app.extensions["shop"]


@bp.get("/dev/pay/<reference>")
def pay_page(reference):
    s = ctx().payments.session(reference)
    if not s:
        raise ApiError(404, "not_found", "Unknown sandbox payment")
    amount = f"{s['amount'] / 100:.2f} {html.escape(s['currency'])}"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Sandbox payment</title><link rel="stylesheet" href="/src/styles/main.css"></head>
<body class="dev-pay"><main class="container narrow"><div class="card">
<p class="badge-test">SANDBOX - no real money</p>
<h1>Test payment</h1><p>Order <strong>{html.escape(s['order_reference'])}</strong> &middot; {amount}</p>
<p class="muted">This stands in for a hosted payment provider page. Real card details are never entered on our site.</p>
<form method="post" action="/dev/pay/{html.escape(reference)}/succeeded"><button class="btn btn-primary" type="submit">Simulate successful payment</button></form>
<form method="post" action="/dev/pay/{html.escape(reference)}/failed"><button class="btn btn-ghost" type="submit">Simulate failed payment</button></form>
</div></main></body></html>"""
    return make_response(page)


@bp.post("/dev/pay/<reference>/<outcome>")
def pay_action(reference, outcome):
    c = ctx()
    s = c.payments.session(reference)
    if not s or outcome not in ("succeeded", "failed"):
        raise ApiError(404, "not_found", "Unknown sandbox payment")
    body = sandbox.build_event_body(type_=f"payment.{outcome}", reference=reference,
                                    order_reference=s["order_reference"], amount_minor=s["amount"], currency=s["currency"])
    # Go through the same signature verification as a real webhook:
    event = c.payments.parse_webhook({sandbox.SIGNATURE_HEADER: sandbox.sign(c.settings.payment_webhook_secret, body)}, body)
    if outcome == "succeeded":
        c.payments.set_status(reference, "paid")
    else:
        c.payments.set_status(reference, "failed")
    payment_processing.process_payment_event(c, event)
    return redirect(s["success_url"] if outcome == "succeeded" else s["cancel_url"], code=303)
