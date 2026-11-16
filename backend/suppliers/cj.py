"""CJdropshipping API 2.0 supplier adapter.

Credentials stay in the server environment. The adapter exchanges CJ_API_KEY for
short-lived cached access credentials and then uses the CJ-Access-Token header.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import urllib.error
import urllib.parse
import urllib.request
import json

from backend.suppliers.base import (
    SupplierAdapter,
    SupplierError,
    SupplierNotConfigured,
    SupplierProduct,
    SupplierPrice,
    SupplierOrderRequest,
    SupplierOrderResult,
    TrackingInfo,
)


class CJApiError(SupplierError):
    pass


_CJ_REQUEST_LOCK = threading.Lock()
_AUTH_COOLDOWN = 300.0


class CJDropshippingAdapter(SupplierAdapter):
    name = "cj"
    supports_live_checks = True
    BASE = "https://developers.cjdropshipping.com/api2.0/v1"

    def __init__(self, api_key: str = "", access_token: str = "", refresh_token: str = ""):
        # Environment values can accidentally contain copied line breaks/spaces.
        # Normalize them before putting secrets into HTTP headers.
        self._api_key = (api_key or "").strip()
        self._access_token = (access_token or "").strip()
        self._refresh_token = (refresh_token or "").strip()
        self._access_expiry = 0.0
        self._refresh_expiry = 0.0
        self._last_request_at = 0.0
        self._min_request_interval = 1.2
        self._token_cache_file = Path(os.getenv("CJ_TOKEN_CACHE", os.getenv("CJ_TOKEN_CACHE_FILE", "/tmp/cj_token_cache.json")))
        self._last_auth_at = 0.0
        self._load_cached_tokens()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _wait_before_request(self) -> None:
        delay = self._min_request_interval - (time.monotonic() - self._last_request_at)
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    def _json(self, method: str, url: str, payload: dict | None = None,
              headers: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        last_error = None
        for attempt in range(3):
            retryable = False
            with _CJ_REQUEST_LOCK:
                self._wait_before_request()
                request = urllib.request.Request(url, data=body, method=method.upper())
                request.add_header("Content-Type", "application/json")
                for key, value in (headers or {}).items():
                    if isinstance(value, str):
                        value = value.strip().splitlines()[0]
                    request.add_header(key, value)
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        raw = response.read().decode("utf-8")
                except urllib.error.HTTPError as exc:
                    raw = exc.read().decode("utf-8", errors="replace")
                    last_error = CJApiError(f"CJ HTTP {exc.code}: {raw[:500]}")
                    retryable = exc.code == 429 or "too many requests" in raw.lower()
                except (urllib.error.URLError, TimeoutError) as exc:
                    raise CJApiError(f"CJ request failed: {exc}") from exc
                else:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise CJApiError("CJ returned invalid JSON") from exc
                    message = str(data.get("message") or "")
                    failed = data.get("code") not in (None, 0, 200) or data.get("success") is False
                    if not failed:
                        return data
                    last_error = CJApiError(message or "CJ API request failed")
                    retryable = "too many requests" in message.lower()
            if not retryable or attempt == 2:
                raise last_error
            time.sleep(2.0 * (attempt + 1))
        raise last_error or CJApiError("CJ API request failed")
    def _load_cached_tokens(self) -> None:
        if self._access_token:
            return
        try:
            data = json.loads(self._token_cache_file.read_text("utf-8"))
            self._access_token = str(data.get("access_token") or "").strip()
            self._refresh_token = str(data.get("refresh_token") or self._refresh_token).strip()
            self._access_expiry = float(data.get("access_expiry") or 0)
            self._refresh_expiry = float(data.get("refresh_expiry") or 0)
            self._last_auth_at = float(data.get("last_auth_at") or 0)
        except (OSError, ValueError, TypeError):
            return

    def _save_cached_tokens(self) -> None:
        if not self._access_token:
            return
        try:
            self._token_cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._token_cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "access_expiry": self._access_expiry,
                "refresh_expiry": self._refresh_expiry,
                "last_auth_at": self._last_auth_at,
            }), encoding="utf-8")
            tmp.replace(self._token_cache_file)
        except OSError:
            pass

    def _authorize(self) -> None:
        if not self.configured:
            raise SupplierNotConfigured("CJ_API_KEY is not configured")

        now = time.time()
        if self._access_token and self._access_expiry == 0:
            self._access_expiry = now + 150 * 24 * 60 * 60
        if self._access_token and now < self._access_expiry - 60:
            return

        if self._refresh_token and now < self._refresh_expiry - 60:
            data = self._json(
                "POST",
                f"{self.BASE}/authentication/refreshAccessToken",
                {"refreshToken": self._refresh_token},
            ).get("data") or {}
        else:
            if self._last_auth_at and now - self._last_auth_at < _AUTH_COOLDOWN:
                raise CJApiError("CJ token request is temporarily rate-limited; reuse CJ_ACCESS_TOKEN/CJ_REFRESH_TOKEN or wait before requesting another token.")
            data = self._json(
                "POST",
                f"{self.BASE}/authentication/getAccessToken",
                {"apiKey": self._api_key},
            ).get("data") or {}

        self._access_token = str(data.get("accessToken", "") or "").strip()
        self._refresh_token = str(data.get("refreshToken", self._refresh_token) or "").strip()
        if not self._access_token:
            raise CJApiError("CJ did not return an access token")

        self._access_expiry = _iso_timestamp(data.get("accessTokenExpiryDate"))
        self._last_auth_at = now
        self._refresh_expiry = _iso_timestamp(data.get("refreshTokenExpiryDate"))
        self._save_cached_tokens()

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        self._authorize()
        return self._json(
            method,
            f"{self.BASE}{path}",
            payload,
            {"CJ-Access-Token": self._access_token},
        )

    def authentication_check(self) -> bool:
        """Exchange the configured API key for a token without exposing either."""
        self._authorize()
        return True

    def list_products(self, keyword: str = "", page: int = 1, size: int = 20,
                      country_code: str = "") -> list[dict]:
        """Fetch products from CJ's current V2 catalog search endpoint."""
        params = {"page": max(1, page), "size": min(max(1, size), 100)}
        if keyword.strip():
            params["keyWord"] = keyword.strip()
        if country_code.strip():
            params["countryCode"] = country_code.strip().upper()
        query = "&".join(
            f"{key}={urllib.parse.quote(str(value))}" for key, value in params.items()
        )
        data = self._call("GET", f"/product/listV2?{query}").get("data") or {}
        # CJ V2 returns: data.content[].productList[].
        # Flatten that structure so the rest of the app can consume normal
        # product rows without knowing CJ's search-result wrapper.
        if isinstance(data, list):
            return data
        content = data.get("content") if isinstance(data, dict) else None
        if isinstance(content, list):
            rows = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("productList"), list):
                    rows.extend(block["productList"])
                elif isinstance(block, dict):
                    rows.append(block)
            return rows
        for key in ("list", "records", "products"):
            if isinstance(data.get(key), list):
                return data[key]
        return []

    def get_variants(self, product_id: str) -> list[dict]:
        query = urllib.parse.urlencode({"pid": product_id})
        data = self._call("GET", f"/product/variant/query?{query}").get("data") or []
        if isinstance(data, dict):
            for key in ("content", "list", "records", "variants"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data] if data.get("vid") else []
        return data if isinstance(data, list) else []

    def get_variant_stock(self, variant_id: str) -> int:
        query = urllib.parse.urlencode({"vid": variant_id})
        data = self._call("GET", f"/product/stock/queryByVid?{query}").get("data") or []
        rows = data if isinstance(data, list) else [data]
        total = 0
        for row in rows:
            if isinstance(row, dict):
                total += _int(row.get("storageNum") or row.get("totalInventory") or row.get("inventory"))
        return total

    def get_product(self, supplier_product_id: str) -> SupplierProduct:
        # CJ's current productDetail endpoint returns variants plus inventory in one call.
        # Prefer it so an import does not make several dependent CJ requests.
        detail = self._call(
            "POST",
            "/product/productDetail/query",
            {"id": str(supplier_product_id)},
        ).get("data") or {}

        variants = detail.get("stanProducts") or []
        inventories = detail.get("variantInventory") or []
        inventory_by_vid = {}
        for item in inventories:
            if not isinstance(item, dict):
                continue
            vid = str(item.get("vid") or "").strip()
            total = 0
            for inv in item.get("inventory") or []:
                if isinstance(inv, dict):
                    total += _int(inv.get("totalInventory") or inv.get("storageNum") or inv.get("inventory"))
            if vid:
                inventory_by_vid[vid] = total

        variant = next((v for v in variants if isinstance(v, dict)), {})
        vid = str(variant.get("id") or variant.get("vid") or supplier_product_id).strip()
        price = _number(variant.get("sellprice") or variant.get("variantSellPrice") or detail.get("sellprice"))
        stock = inventory_by_vid.get(vid, _int(variant.get("totalInventory")))
        images = []
        if detail.get("bigimg"):
            images.append(detail["bigimg"])
        for v in variants:
            if isinstance(v, dict) and v.get("img") and v["img"] not in images:
                images.append(v["img"])

        # If productDetail does not expose a usable inventory value, use the
        # documented variant + stock endpoints as a single fallback path.
        if stock <= 0 and vid != str(supplier_product_id):
            stock = self.get_variant_stock(vid)

        return SupplierProduct(
            supplier_product_id=vid,
            title=detail.get("nameen") or detail.get("nameEn") or detail.get("name") or "",
            url=detail.get("supplierlink") or detail.get("productUrl") or "",
            price_minor=round(price * 100),
            currency="USD",
            stock=stock,
            images=tuple(images),
        )

    def get_price(self, supplier_product_id: str) -> SupplierPrice:
        product = self.get_product(supplier_product_id)
        return SupplierPrice(product.price_minor, product.currency)

    def get_stock(self, supplier_product_id: str) -> int:
        return self.get_product(supplier_product_id).stock

    def create_supplier_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:
        # supplier_product_id is expected to be a CJ variant ID for the MVP.
        address = request.ship_to_address
        payload = {
            "orderNumber": request.reference,
            "shippingCustomerName": request.ship_to_name,
            "shippingCountryCode": request.ship_to_country,
            "shippingCountry": request.ship_to_country,
            "shippingProvince": address.get("province", ""),
            "shippingCity": address.get("city", ""),
            "shippingAddress": address.get("address", address.get("line1", "")),
            "shippingZip": address.get("postal_code", address.get("zip", "")),
            "shippingPhone": address.get("phone", ""),
            "remark": "Automated order from our storefront",
            "products": [{"vid": request.supplier_product_id, "quantity": request.quantity}],
            "payType": 3,
            "isSandbox": 1,
        }
        data = self._call("POST", "/shopping/order/createOrderV3", payload).get("data") or {}
        order_id = str(data.get("orderId") or data.get("orderCode") or "")
        if not order_id:
            raise CJApiError("CJ created no order ID")
        return SupplierOrderResult(
            supplier_order_id=order_id,
            status=data.get("orderStatus") or "CREATED",
            raw=data,
        )

    def get_tracking(self, supplier_order_id: str) -> TrackingInfo:
        data = self._call(
            "POST",
            "/logistic/trackInfo",
            {"cjOrderCode": supplier_order_id},
        ).get("data") or {}
        events = data.get("trackingInfo") or data.get("trackInfo") or []
        return TrackingInfo(
            carrier=data.get("logisticName") or data.get("carrier"),
            tracking_number=data.get("trackingNumber") or data.get("trackNumber"),
            status=data.get("status") or data.get("orderStatus") or "UNKNOWN",
            events=tuple(events) if isinstance(events, list) else (),
        )


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if "--" in text:
        text = text.split("--", 1)[0].strip()
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _iso_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return time.time() + 180 * 24 * 60 * 60
