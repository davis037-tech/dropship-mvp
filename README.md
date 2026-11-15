# Single-product dropshipping MVP

`1688 supplier → backend → storefront → checkout → verified payment → order dashboard → manual supplier approval → 1688 purchase (by hand) → tracking → customer`

Flask + SQL backend, dependency-free vanilla-JS storefront and admin dashboard (no build step). Initial product: *Flower Garden Snuffle Mat for Dogs* (generic/OEM). Store name is a placeholder (`STORE_NAME`).

## What is real and what is not (read this first)

| Area | Status |
|---|---|
| Storefront, cart, checkout, order tracking page, admin dashboard | Built and browser-tested (mobile + desktop) |
| API, pricing engine, stock/price guard, product sync, order workflow, webhooks (signed, idempotent) | Built, 110 automated tests |
| Database schema | Verified on SQLite (tests) **and** a real PostgreSQL 16 (schema + every upsert statement run by hand). The Python PostgreSQL driver path (`psycopg`) could **not** be exercised here (not installable offline) - run the test-suite against your Postgres once (see Testing). |
| **1688 integration** | **NOT connected.** `suppliers/platform_1688.py` is an honest placeholder: it raises "not configured / not implemented". There is no 1688 API access in this project. Use `SUPPLIER_MODE=manual` until you have it. |
| Payments | `sandbox` provider (dev only) is fully working. `stripe` provider is a **reference implementation, untested against Stripe** (webhook-signature logic is unit-tested; HTTP calls are not). Test with Stripe test keys first. |
| Transactional emails | Not included. Buyers get a status link on the post-payment redirect; add an email provider to send it. |
| Docker | Files written, **not run** (no Docker daemon in the build environment). |
| Legal (returns, privacy, terms) | Placeholders only. Nothing invented. |
| Product photos, real costs, shipping rates, delivery estimates | Yours to supply; sample values are clearly fictional. |

## Layout

```
backend/
  api/        routes: public.py, admin.py, dev.py (sandbox only), pages.py (HTML + SEO), schemas.py, security.py
  auth/       scrypt password hashing, JWT admin tokens
  config/     settings.py  (ALL env vars, production safety checks)
  database/   db.py (sqlite/postgres), migrate.py, seed.py (dev sample data)
  models/     repositories + public/admin/customer serializers
  payments/   base.py (interface), provider.py (factory), sandbox.py, stripe_provider.py
  services/   pricing, product_sync, stock_guard, checkout, payment_processing, order_workflow, fulfillment
  suppliers/  base.py (SupplierAdapter), mock.py (dev), manual.py, platform_1688.py (placeholder)
frontend/     index/product/cart/checkout/order .html, admin/index.html, src/{components,pages,services,config,styles}
migrations/   001_init.sql
tests/        pytest suite + tests/e2e/run_e2e.py (Playwright)
```

## Local setup (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt          # Flask, pydantic, PyJWT (+ psycopg, gunicorn, pytest, playwright)
cp .env.example .env
python -m backend.auth.hash_password         # paste output into ADMIN_PASSWORD_HASH; set ADMIN_EMAIL in .env
python -m backend.database.seed              # creates ./data/dev.db with the sample product + sample shipping zones
flask --app backend.wsgi run --port 5000     # http://localhost:5000   admin: http://localhost:5000/admin
```

Development defaults: `SUPPLIER_MODE=mock` (fictional supplier, no network) and `PAYMENT_PROVIDER=sandbox` (a fake hosted payment page at `/dev/pay/...`). "Paying" there sends a **signed webhook** through the same verification path a real provider uses. Both are refused when `APP_ENV=production`.

## Database

* Dev: SQLite file (default `./data/dev.db`), migrations auto-run.
* Production: PostgreSQL. `DATABASE_URL=postgresql://user:pass@host:5432/db`, then `python -m backend.database.migrate` (or `docker compose run --rm migrate`). Migrations are plain SQL in `migrations/`; add `002_*.sql` for changes.
* Money is stored as integer minor units (cents); timestamps are ISO-8601 UTC text; JSON fields are text. All queries use bound parameters.
* Tables: `products, supplier_products, customers, orders, order_items, shipping_zones, settings, payment_events (idempotency ledger), order_events (audit trail)`.
* Deliberate deviations from the brief: the buyer's address is stored **per order** (not on `customers`) so old orders keep their original address and we store less personal data; `orders` has a public random `reference`; internal cost snapshots live on `order_items`/`orders`.

## How the rules in the brief are enforced

* **Public API never leaks internals** – `models/products.to_public` is an explicit allow-list; customer order view has its own serializer. Tests assert no cost/profit/supplier/secret strings in any public response.
* **Price is server-side only** – the cart sends slugs + quantities; totals come from the database. Extra fields such as `price` are ignored (tested).
* **Stock protection** (`services/stock_guard.py`) – at checkout: DB status/stock → fresh supplier price + stock (via sync) → refuse if unavailable, flagged, or below `MIN_ACCEPTABLE_MARGIN` → only then create the order. Refused products are flagged `needs_review` and hidden from purchase; customers just see "currently unavailable". If the supplier cannot be reached: `SUPPLIER_CHECK_POLICY=strict` blocks; `cache` allows only data younger than `STOCK_CACHE_MAX_AGE_MINUTES`.
* **Payment** – orders are created `pending_payment`. Only a webhook with a valid signature (or a server-to-server lookup at the provider when the buyer returns) can mark an order paid; amount and currency must match the order. Same event id twice is a no-op (`payment_events` unique key); a second event for an already-paid order is a no-op.
* **Manual supplier approval** – `PAID → AWAITING_SUPPLIER_APPROVAL → SUPPLIER_APPROVED → SUPPLIER_ORDERED → SHIPPED → DELIVERED`. Customers cannot trigger supplier purchases. Approval runs a fresh supplier check and warns about stock/price problems. Cancelling a paid order requires an explicit refund decision; refunds go through the payment module.
* **Future automation** – `services/fulfillment.py`: `ManualFulfillment` today; `AutomaticFulfillment` (calls `SupplierAdapter.create_supplier_order`) is selected by `FULFILLMENT_MODE=automatic` and only allowed with `SUPPLIER_MODE=1688`. Admin approval stays mandatory.

### Supplier modes

| `SUPPLIER_MODE` | Use | Live price/stock checks |
|---|---|---|
| `mock` | development/tests only (refuses production) | simulated |
| `manual` | **production without 1688 API access** | none – *you* keep stock and supplier cost correct in the admin. Checkout trusts those values, so update them whenever 1688 changes. |
| `1688` | after you implement the adapter | yes |

### Pricing

`landed = supplier_cost × FX + estimated shipping + agent fee + other costs`,
`price = (landed + PAYMENT_FIXED_FEE + AD_COST_ALLOWANCE) / (1 − PAYMENT_FEE_PERCENT − REFUND_RESERVE_PERCENT − TARGET_MARGIN)`, rounded up to `.99` if `PRICE_ENDING_CENTS=99`. It is not "×4". The admin product page shows the margin and the multiple of landed cost, so you can tune `TARGET_MARGIN` / `AD_COST_ALLOWANCE` toward your ~4× goal. Sync recalculates the *recommended* price but never silently changes the retail price. Shipping revenue is not used to justify product price.

## Where to insert things later

| What | Where |
|---|---|
| 1688 API credentials | `.env`: `SUPPLIER_1688_API_KEY`, `_API_SECRET`, `_ACCESS_TOKEN` (named `SUPPLIER_1688_*` because env names starting with a digit break shells/compose). Implement the `TODO[1688]` methods in `backend/suppliers/platform_1688.py`, then `SUPPLIER_MODE=1688`. |
| Payment credentials | `.env`: `PAYMENT_PROVIDER=stripe`, `PAYMENT_SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET`, `PAYMENT_MODE=live`. Point the provider's webhook to `https://YOUR-DOMAIN/api/webhooks/payment`. Another provider = one new class implementing `payments/base.py` + one line in `payments/provider.py`. |
| Product info, photos, costs | Admin → Products (name, copy, specifications, images as `https://… | alt text`, supplier id, costs, price). |
| Shipping rates & delivery estimates | Admin → *Shipping & policies*. Leave delivery days empty until you have real data – the storefront then says "to be confirmed". Only enabled countries can order. |
| Pricing settings | `.env` (`TARGET_MARGIN`, `PAYMENT_FEE_PERCENT`, `PAYMENT_FIXED_FEE`, `REFUND_RESERVE_PERCENT`, `AD_COST_ALLOWANCE`, `MIN_ACCEPTABLE_MARGIN`, `PRICE_REVIEW_THRESHOLD_PERCENT`, `SUPPLIER_FX_RATE`). Percentages are in percent units; money in major units. |
| Returns/refund policy | Admin → *Shipping & policies* (placeholder text until you write it; get legal review). |
| Store name / currency / domain | `STORE_NAME`, `STORE_CURRENCY`, `PUBLIC_BASE_URL`. |

Supplier cost sync: run `python -m backend.services.product_sync` from cron (e.g. every 15–60 min) once a live supplier adapter exists, or press "Sync all" in the admin.

## Testing

```bash
pytest                         # 110 backend tests: pricing, API, checkout guards, payments/webhooks, admin auth, supplier+sync, security, production-config guards
python tests/e2e/run_e2e.py    # browser run (Playwright/Chromium): full buy → pay → approve → ship → track flow, mobile+desktop overflow, CSP, XSS
```
To test the PostgreSQL path: `DATABASE_URL=postgresql://… python -m backend.database.migrate` against an empty database, then seed and click through, or adapt `tests/conftest.py` to use that URL.

## Deployment (outline)

1. Provision PostgreSQL and a host (VM, Fly, Render, ECS…). Put TLS in front (Caddy/nginx/Cloudflare) and set `TRUST_PROXY=true`.
2. Create `.env` with production values: `APP_ENV=production`, 32+ char `SECRET_KEY`, PostgreSQL `DATABASE_URL`, https `PUBLIC_BASE_URL`, admin email + hash, live payment keys, `SUPPLIER_MODE=manual` (or `1688`). The app **refuses to start** if any of these are unsafe (mock supplier, sandbox payments, test payment mode, short secret, non-Postgres DB, http URL, wildcard CORS…).
3. `docker compose build && docker compose run --rm migrate && docker compose up -d` (or `pip install -r backend/requirements.txt`, run migrations, `gunicorn backend.wsgi:app`).
4. Log in at `/admin`, add shipping zones, create the product with real photos, costs and price, set returns text.
5. Register the webhook URL with your payment provider and place a test order with *test* keys before going live.

Limits to know: the rate limiter is in-process (use an edge/Redis limiter if you scale to many workers); the sandbox provider keeps state in memory; one flat shipping charge per order per country; zero-decimal currencies are unsupported; local stock is decremented when payment is confirmed and is not restored automatically on cancellation (stock is re-synced from the supplier / set in admin).
