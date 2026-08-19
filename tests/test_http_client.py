"""Backoff and replay behaviour.

These paths are demonstrated live during the defence, so they are tested with a
fake clock rather than met for the first time in front of an audience.
"""

import json

import httpx
import pytest

from procurement_mcp.errors import ErrorCode, ToolError
from procurement_mcp.http import ProzorroClient, ReplayClient, RetryPolicy, fixture_key


class FakeClock:
    """Records every sleep instead of performing it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


def responses(*statuses: int, body: dict | None = None):
    """Fetcher returning the given statuses in order, then repeating the last."""
    queue = list(statuses)
    calls: list[str] = []

    def fetch(url: str) -> httpx.Response:
        calls.append(url)
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(
            status,
            json=body if body is not None else {"data": []},
            request=httpx.Request("GET", url),
        )

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def client(fetch, clock: FakeClock, **kwargs) -> ProzorroClient:
    return ProzorroClient(
        fetcher=fetch,
        sleep=clock.sleep,
        clock=clock.time,
        jitter=lambda: 1.0,
        **kwargs,
    )


def test_retries_transient_failures_then_succeeds():
    clock = FakeClock()
    fetch = responses(503, 503, 200, body={"data": [{"id": "x"}]})
    result = client(fetch, clock).get_json("/tenders")

    assert result == {"data": [{"id": "x"}]}
    assert len(fetch.calls) == 3


def test_backoff_delay_grows_between_attempts():
    clock = FakeClock()
    fetch = responses(503, 503, 200)
    client(fetch, clock, requests_per_second=0).get_json("/tenders")

    assert clock.sleeps == [1.0, 2.0], "expected exponential growth from the base delay"


def test_jitter_is_applied_to_the_delay():
    clock = FakeClock()
    fetch = responses(503, 200)
    ProzorroClient(
        fetcher=fetch,
        sleep=clock.sleep,
        clock=clock.time,
        jitter=lambda: 0.25,
        requests_per_second=0,
    ).get_json("/tenders")

    assert clock.sleeps == [0.25], "full-jitter policy should scale the window, not ignore it"


def test_delay_is_capped():
    clock = FakeClock()
    fetch = responses(503)
    policy = RetryPolicy(max_attempts=6, base_delay=1.0, multiplier=10.0, max_delay=5.0)
    with pytest.raises(ToolError):
        client(fetch, clock, retry=policy, requests_per_second=0).get_json("/tenders")

    assert max(clock.sleeps) <= 5.0


def test_exhausted_retries_raise_upstream_unavailable_not_an_exception_leak():
    clock = FakeClock()
    fetch = responses(503)
    with pytest.raises(ToolError) as excinfo:
        client(fetch, clock, requests_per_second=0).get_json("/tenders")

    assert excinfo.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert excinfo.value.retryable is True
    assert excinfo.value.details["attempts"] == 4


def test_rate_limited_status_is_reported_as_its_own_code():
    clock = FakeClock()
    fetch = responses(429)
    with pytest.raises(ToolError) as excinfo:
        client(fetch, clock, requests_per_second=0).get_json("/tenders")

    assert excinfo.value.code == ErrorCode.RATE_LIMITED


def test_missing_document_is_not_found_and_is_not_retried():
    clock = FakeClock()
    fetch = responses(404)
    with pytest.raises(ToolError) as excinfo:
        client(fetch, clock, requests_per_second=0).get_json("/tenders/deadbeef")

    assert excinfo.value.code == ErrorCode.NOT_FOUND
    assert excinfo.value.retryable is False
    assert len(fetch.calls) == 1, "a 404 is an answer, not a transient failure"


def test_network_error_is_retried_then_surfaced():
    clock = FakeClock()
    attempts: list[str] = []

    def fetch(url: str) -> httpx.Response:
        attempts.append(url)
        raise httpx.ConnectError("no route to host", request=httpx.Request("GET", url))

    with pytest.raises(ToolError) as excinfo:
        client(fetch, clock, requests_per_second=0).get_json("/tenders")

    assert excinfo.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert len(attempts) == 4


def test_rate_limit_spaces_requests():
    clock = FakeClock()
    fetch = responses(200)
    c = client(fetch, clock, requests_per_second=2.0)
    c.get_json("/tenders")
    c.get_json("/tenders")

    assert clock.sleeps == [0.5], "second call should wait out the remaining interval"


def test_replay_serves_recorded_response(tmp_path):
    params = {"limit": 2}
    (tmp_path / fixture_key("/tenders", params)).write_text(
        json.dumps({"data": [{"id": "recorded"}]}), encoding="utf-8"
    )

    assert ReplayClient(tmp_path).get_json("/tenders", params) == {"data": [{"id": "recorded"}]}


def test_replay_reports_missing_fixture_instead_of_falling_back(tmp_path):
    with pytest.raises(ToolError) as excinfo:
        ReplayClient(tmp_path).get_json("/tenders", {"limit": 2})

    assert excinfo.value.code == ErrorCode.FIXTURE_MISSING
    assert "expected_file" in excinfo.value.details


def test_fixture_key_is_order_independent():
    assert fixture_key("/tenders", {"a": 1, "b": 2}) == fixture_key("/tenders", {"b": 2, "a": 1})
