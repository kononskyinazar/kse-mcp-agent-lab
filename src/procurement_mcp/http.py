"""Rate-limited HTTP access to the Prozorro / OpenProcurement public API.

Three behaviours matter here and each is tested rather than assumed:

* one request per second by default, because the API returned 503 under rapid
  sequential calls during design probing;
* exponential backoff with jitter on 429 and 503, ending in
  UPSTREAM_UNAVAILABLE rather than an exception escaping the tool boundary;
* offline replay from recorded responses, selected by configuration, which
  never silently falls back to the network.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlencode

import httpx

from .errors import ErrorCode, ToolError

DEFAULT_BASE_URL = "https://public.api.openprocurement.org/api/2.5"
USER_AGENT = "kse-mcp-lab/0.1 (KSE coursework; contact via repository)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class Fetcher(Protocol):
    """Minimal seam so tests and replay mode can stand in for the network."""

    def __call__(self, url: str) -> httpx.Response: ...


def fixture_key(path: str, params: dict[str, Any] | None) -> str:
    """Stable name for a recorded response.

    Keyed on path plus sorted parameters so the same logical request always
    resolves to the same file regardless of argument order.
    """
    query = urlencode(sorted((params or {}).items()))
    digest = hashlib.sha256(f"{path}?{query}".encode()).hexdigest()[:16]
    slug = path.strip("/").replace("/", "_") or "root"
    return f"{slug}.{digest}.json"


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 1.0
    multiplier: float = 2.0
    max_delay: float = 30.0


class ProzorroClient:
    """Live client. Enforces the rate limit and the retry policy."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        requests_per_second: float = 1.0,
        timeout: float = 15.0,
        retry: RetryPolicy | None = None,
        fetcher: Fetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self.retry = retry or RetryPolicy()
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._last_request_at: float | None = None
        self._owns_client = fetcher is None
        self._client = (
            httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
            if fetcher is None
            else None
        )
        self._fetch: Fetcher = fetcher or (lambda url: self._client.get(url))  # type: ignore[union-attr]

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> "ProzorroClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        if self._last_request_at is not None:
            elapsed = self._clock() - self._last_request_at
            remaining = self.min_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _delay_for(self, attempt: int) -> float:
        """Exponential backoff with full jitter, capped."""
        window = min(self.retry.base_delay * self.retry.multiplier**attempt, self.retry.max_delay)
        return window * self._jitter()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"

        last_status: int | None = None
        for attempt in range(self.retry.max_attempts):
            self._throttle()
            try:
                response = self._fetch(url)
            except httpx.RequestError as exc:
                last_status = None
                if attempt == self.retry.max_attempts - 1:
                    raise ToolError(
                        ErrorCode.UPSTREAM_UNAVAILABLE,
                        f"request to {path} failed: {exc.__class__.__name__}",
                        {"path": path, "attempts": attempt + 1},
                    ) from exc
                self._sleep(self._delay_for(attempt))
                continue

            if response.status_code in RETRY_STATUSES:
                last_status = response.status_code
                if attempt == self.retry.max_attempts - 1:
                    break
                self._sleep(self._delay_for(attempt))
                continue

            if response.status_code == 404:
                raise ToolError(
                    ErrorCode.NOT_FOUND,
                    f"{path} not found upstream",
                    {"path": path, "status": 404},
                )
            if response.status_code >= 400:
                raise ToolError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    f"{path} returned HTTP {response.status_code}",
                    {"path": path, "status": response.status_code},
                )
            return response.json()

        code = ErrorCode.RATE_LIMITED if last_status == 429 else ErrorCode.UPSTREAM_UNAVAILABLE
        raise ToolError(
            code,
            f"{path} still failing after {self.retry.max_attempts} attempts",
            {"path": path, "status": last_status, "attempts": self.retry.max_attempts},
        )


class ReplayClient:
    """Serves recorded responses. Never reaches the network, never invents one."""

    def __init__(self, fixture_dir: Path) -> None:
        self.fixture_dir = Path(fixture_dir)

    def close(self) -> None:  # symmetry with ProzorroClient
        return None

    def __enter__(self) -> "ReplayClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        target = self.fixture_dir / fixture_key(path, params)
        if not target.exists():
            raise ToolError(
                ErrorCode.FIXTURE_MISSING,
                f"no recorded response for {path}",
                {"path": path, "params": params or {}, "expected_file": target.name},
            )
        return json.loads(target.read_text(encoding="utf-8"))
