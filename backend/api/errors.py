"""API error type + JSON error envelope."""
from __future__ import annotations

from typing import Any, Optional


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Optional[Any] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details

    def to_dict(self) -> dict:
        body: dict = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return {"error": body}
