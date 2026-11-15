"""Shipping zones (per destination country) and key/value settings (returns policy)."""
from __future__ import annotations

from typing import Optional

from backend.database.db import Database
from backend.models.common import jloads, jdumps, minor_to_major, utcnow


def get_zone(db: Database, country: str, *, enabled_only: bool = True) -> Optional[dict]:
    sql = "SELECT * FROM shipping_zones WHERE country = ?" + (" AND enabled = 1" if enabled_only else "")
    return db.fetch_one(sql, [country.upper()])


def list_zones(db: Database, *, enabled_only: bool = False) -> list:
    sql = "SELECT * FROM shipping_zones" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY country"
    return db.fetch_all(sql)


def upsert_zone(db: Database, country: str, *, shipping_cost: int, delivery_days_min: Optional[int],
                delivery_days_max: Optional[int], tracking_available: bool, enabled: bool) -> None:
    now = utcnow()
    db.execute(
        """INSERT INTO shipping_zones (country, shipping_cost, delivery_days_min, delivery_days_max,
                                       tracking_available, enabled, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT (country) DO UPDATE SET shipping_cost = excluded.shipping_cost,
               delivery_days_min = excluded.delivery_days_min, delivery_days_max = excluded.delivery_days_max,
               tracking_available = excluded.tracking_available, enabled = excluded.enabled,
               updated_at = excluded.updated_at""",
        [country.upper(), shipping_cost, delivery_days_min, delivery_days_max, int(tracking_available), int(enabled), now, now],
    )


def delete_zone(db: Database, country: str) -> int:
    return db.execute("DELETE FROM shipping_zones WHERE country = ?", [country.upper()])


def zone_public(z: dict) -> dict:
    return {
        "country": z["country"],
        "shipping_cost": minor_to_major(z["shipping_cost"]),
        "delivery_days_min": z["delivery_days_min"],
        "delivery_days_max": z["delivery_days_max"],
        "tracking_available": bool(z["tracking_available"]),
    }


def zone_admin(z: dict) -> dict:
    return {**zone_public(z), "enabled": bool(z["enabled"])}


# ---------------------------------------------------------------- settings
RETURN_SETTING_KEYS = (
    "returns.enabled",             # "true"/"false" - is a returns window offered at all
    "returns.window_days",         # integer or ""
    "returns.return_shipping_paid_by",  # "customer" | "store" | ""
    "returns.refund_method",       # free text, e.g. "original payment method"
    "returns.policy_text",         # the final legal text - intentionally a placeholder
)
DEFAULT_POLICY_PLACEHOLDER = "Our returns and refunds policy has not been published yet."


def get_settings(db: Database) -> dict:
    rows = {r["key"]: r["value"] for r in db.fetch_all("SELECT key, value FROM settings")}
    out = {k: rows.get(k, "") for k in RETURN_SETTING_KEYS}
    if not out["returns.policy_text"]:
        out["returns.policy_text"] = DEFAULT_POLICY_PLACEHOLDER
    return out


def set_setting(db: Database, key: str, value: str) -> None:
    if key not in RETURN_SETTING_KEYS:
        raise KeyError(key)
    db.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        [key, value, utcnow()],
    )
