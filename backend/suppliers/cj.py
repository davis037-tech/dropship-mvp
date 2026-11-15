"""CJdropshipping API 2.0 supplier adapter.

Credentials stay in the server environment. The adapter exchanges CJ_API_KEY for
short-lived cached access credentials and then uses the CJ-Access-Token header.
"""
from __future__ import annotations

import time
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


class CJDropshippingAdapter(SupplierAdapter):
    name = "cj"
    supports_live_checks = True
    BASE = "https://developers.cjdropshipping.com/api2.0/v1"

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._access_token = ""
        self._refresh_token = ""
        self._access_expiry = 0.0
        self._refresh_expiry = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _json(self, method: str, url: str, payload: dict | None = None,
              headers: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method.upper())
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise CJApiError(f"CJ HTTP {exc.code}: {raw[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CJApiError(f"CJ request failed: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CJApiError("CJ returned invalid JSON") from exc
        if data.get("code") not in (None, 0, 200) or data.get("success") is False:
            raise CJApiError(data.get("message") or "CJ API request failed")
        return data

    def _authorize(self) -> None:
        if not self.configured:
            raise SupplierNotConfigured("CJ_API_KEY is not configured")

        now = time.time()
        if self._access_token and now < self._access_expiry - 60:
            return

        if self._refresh_token and now < self._refresh_expiry - 60:
            data = self._json(
                "POST",
                f"{self.BASE}/authentication/refreshAccessToken",
                {"refreshToken": self._refresh_token},
            ).get("data") or {}
        else:
            data = self._json(
                "POST",
                f"{self.BASE}/authentication/getAccessToken",
                {"apiKey": self._api_key},
            ).get("data") or {}

        self._access_token = data.get("accessToken", "")
        self._refresh_token = data.get("refreshToken", self._refresh_token)
        if not self._access_token:
            raise CJApiError("CJ did not return an access token")

        self._access_expiry = _iso_timestamp(data.get("accessTokenExpiryDate"))
        self._refresh_expiry = _iso_timestamp(data.get("refreshTokenExpiryDate"))

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
        if isinstance(data, list):
            return data
        for key in ("list", "records", "content", "products"):
            if isinstance(data.get(key), list):
                return data[key]
        return []

    def get_product(self, supplier_product_id: str) -> SupplierProduct:
        # CJ documents Product Query as GET /product/query?pid=...
        query = urllib.parse.urlencode({"pid": supplier_product_id})
        data = self._call("GET", f"/product/query?{query}").get("data") or {}
        product = data.get("content", data) if isinstance(data, dict) else {}
        variants = product.get("variants") or product.get("variantList") or []
        variant = variants[0] if variants else {}
        price = _number(variant.get("variantSellPrice") or product.get("sellPrice"))
        stock = _int(
            variant.get("variantAvailableInventory")
            or variant.get("variantStock")
            or product.get("availableInventory")
            or product.get("inventory")
        )
        images = tuple(product.get("productImageSet") or product.get("images") or ())
        if product.get("bigImage") and product["bigImage"] not in images:
            images = (product["bigImage"], *images)
        return SupplierProduct(
            supplier_product_id=str(supplier_product_id),
            title=product.get("productNameEn") or product.get("productName") or product.get("name") or "",
            url=product.get("productUrl") or "",
            price_minor=round(price * 100),
            currency="USD",
            stock=stock,
            images=images,
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
    try:
        return float(value or 0)
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
