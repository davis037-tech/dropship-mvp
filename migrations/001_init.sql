-- Initial schema. Portable across SQLite (dev/tests) and PostgreSQL (production).
--   {PK}  is replaced by the dialect's auto-increment primary key.
--   Money columns are INTEGER minor units (cents). Timestamps are ISO-8601 UTC TEXT.
--   JSON columns (images, specifications, shipping_address) are TEXT holding JSON.

CREATE TABLE products (
    id {PK},
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    supplier_product_id TEXT,
    supplier_platform TEXT NOT NULL DEFAULT '1688',
    supplier_currency TEXT NOT NULL DEFAULT 'CNY',
    supplier_cost INTEGER NOT NULL DEFAULT 0,             -- minor units of supplier_currency
    estimated_shipping_cost INTEGER NOT NULL DEFAULT 0,   -- minor units of store currency
    agent_fee INTEGER NOT NULL DEFAULT 0,                 -- minor units of store currency
    other_supplier_costs INTEGER NOT NULL DEFAULT 0,      -- minor units of store currency
    landed_cost INTEGER NOT NULL DEFAULT 0,               -- minor units of store currency (computed)
    recommended_price INTEGER,                            -- computed by the pricing engine
    retail_price INTEGER NOT NULL DEFAULT 0,              -- what customers pay (admin-approved)
    currency TEXT NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','active','unavailable','needs_review','archived')),
    review_reason TEXT,
    images TEXT NOT NULL DEFAULT '[]',
    specifications TEXT NOT NULL DEFAULT '[]',
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE supplier_products (
    id {PK},
    supplier TEXT NOT NULL,
    supplier_product_id TEXT NOT NULL,
    product_url TEXT,
    supplier_currency TEXT NOT NULL DEFAULT 'CNY',
    supplier_cost INTEGER,
    supplier_stock INTEGER,
    last_checked TEXT,
    status TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('unknown','ok','out_of_stock','error')),
    last_error TEXT,
    UNIQUE (supplier, supplier_product_id)
);

CREATE TABLE customers (
    id {PK},
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE orders (
    id {PK},
    reference TEXT NOT NULL UNIQUE,                       -- public, non-guessable order number
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'pending_payment' CHECK (status IN (
        'pending_payment','awaiting_supplier_approval','supplier_approved',
        'supplier_ordered','shipped','delivered','cancelled','refunded')),
    payment_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (payment_status IN ('pending','paid','failed','refunded','review')),
    subtotal INTEGER NOT NULL,
    shipping_cost INTEGER NOT NULL,
    total INTEGER NOT NULL,
    currency TEXT NOT NULL,
    payment_provider TEXT,
    payment_reference TEXT UNIQUE,
    supplier_status TEXT NOT NULL DEFAULT 'none'
        CHECK (supplier_status IN ('none','pending_approval','approved','ordered','cancelled')),
    supplier_order_id TEXT,
    tracking_number TEXT,
    tracking_carrier TEXT,
    tracking_url TEXT,
    shipping_name TEXT NOT NULL,
    shipping_address TEXT NOT NULL,                       -- JSON snapshot for this order only
    shipping_country TEXT NOT NULL,
    est_cost INTEGER NOT NULL DEFAULT 0,                  -- internal: landed cost snapshot
    est_fees INTEGER NOT NULL DEFAULT 0,                  -- internal: payment fee + reserves
    est_profit INTEGER NOT NULL DEFAULT 0,                -- internal
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_note TEXT,
    paid_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE TABLE order_items (
    id {PK},
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price INTEGER NOT NULL,
    unit_landed_cost INTEGER NOT NULL,                    -- internal snapshot
    created_at TEXT NOT NULL
);
CREATE INDEX idx_order_items_order ON order_items(order_id);

CREATE TABLE shipping_zones (
    id {PK},
    country TEXT NOT NULL UNIQUE,                         -- ISO 3166-1 alpha-2
    shipping_cost INTEGER NOT NULL DEFAULT 0,             -- what the customer pays, minor units
    delivery_days_min INTEGER,                            -- NULL = not configured (never invented)
    delivery_days_max INTEGER,
    tracking_available INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Idempotency ledger: one row per payment-provider notification ever processed.
CREATE TABLE payment_events (
    id {PK},
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payment_reference TEXT,
    outcome TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (provider, event_id)
);

CREATE TABLE order_events (
    id {PK},
    order_id INTEGER NOT NULL REFERENCES orders(id),
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_order_events_order ON order_events(order_id);
