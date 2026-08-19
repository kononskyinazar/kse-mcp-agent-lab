"""Error taxonomy shared by every tool.

The contract the assignment cares about: a failure and a successful-but-empty
result must never be expressible by the same response. Failures raise ToolError
and are serialised into an ``error`` object; empty results are ordinary payloads
carrying ``result_count: 0``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ErrorCode:
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    FIXTURE_MISSING = "FIXTURE_MISSING"
    DATA_INTEGRITY = "DATA_INTEGRITY"


RETRYABLE = frozenset({ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.RATE_LIMITED})


@dataclass
class ToolError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            },
        }


def invalid_input(message: str, **details: Any) -> ToolError:
    return ToolError(ErrorCode.INVALID_INPUT, message, details)


def not_found(message: str, **details: Any) -> ToolError:
    return ToolError(ErrorCode.NOT_FOUND, message, details)


def data_integrity(message: str, **details: Any) -> ToolError:
    return ToolError(ErrorCode.DATA_INTEGRITY, message, details)
