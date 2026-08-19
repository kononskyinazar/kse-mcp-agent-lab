"""Sweep behaviour: ascending resumption, dedupe, allow-list, manifest honesty."""

import gzip
import json

import pytest

from procurement_mcp.harvest import (
    SweepStats,
    count_buyers,
    fetch_documents,
    iter_feed,
    sweep_buyers,
    write_dataset,
)


def row(uuid, edrpou, tender_id="UA-1", status="complete", name="Buyer"):
    return {
        "id": uuid,
        "tenderID": tender_id,
        "status": status,
        "procuringEntity": {"identifier": {"id": edrpou, "legalName": name}},
        "dateModified": "2026-08-19T10:00:00+03:00",
    }


class StubFeed:
    """Serves prepared feed pages and records the offsets it was asked for."""

    def __init__(self, pages, documents=None):
        self.pages = pages
        self.documents = documents or {}
        self.offsets = []
        self.document_calls = []

    def get_json(self, path, params=None):
        if path.startswith("/tenders/"):
            uuid = path.rsplit("/", 1)[1]
            self.document_calls.append(uuid)
            if uuid not in self.documents:
                raise KeyError(uuid)
            return {"data": self.documents[uuid]}
        offset = (params or {}).get("offset")
        self.offsets.append(offset)
        return self.pages[len(self.offsets) - 1]


def page(rows, next_offset):
    payload = {"data": rows}
    if next_offset is not None:
        payload["next_page"] = {"offset": next_offset}
    return payload


def test_feed_follows_the_server_cursor_forwards():
    feed = StubFeed([
        page([row("a", "111")], "off-2"),
        page([row("b", "111")], "off-3"),
        page([], None),
    ])
    rows = list(iter_feed(feed, start_offset="off-1"))

    assert [r.uuid for r in rows] == ["a", "b"]
    assert feed.offsets == ["off-1", "off-2", "off-3"], "must resume from the returned offset"


def test_feed_stops_when_the_cursor_stops_advancing():
    feed = StubFeed([page([row("a", "111")], "same"), page([row("b", "111")], "same")])
    stats = SweepStats()
    rows = list(iter_feed(feed, start_offset="same", stats=stats))

    assert [r.uuid for r in rows] == ["a"]
    assert stats.stopped_because == "feed_exhausted"


def test_max_requests_is_reported_not_hidden():
    feed = StubFeed([page([row(str(i), "111")], f"off-{i + 1}") for i in range(5)])
    stats = SweepStats()
    list(iter_feed(feed, start_offset="off-0", max_requests=2, stats=stats))

    assert stats.requests == 2
    assert stats.stopped_because == "max_requests_reached", "a truncated sweep must say so"


def test_sweep_keeps_only_allow_listed_buyers():
    feed = StubFeed([
        page([row("a", "111"), row("b", "999"), row("c", "222")], "off-2"),
        page([], None),
    ])
    kept, stats = sweep_buyers(feed, edrpous={"111", "222"}, start_offset="off-1")

    assert set(kept) == {"a", "c"}
    assert stats.rows_seen == 3
    assert stats.rows_matched == 2


def test_sweep_dedupes_repeated_modifications_of_one_tender():
    feed = StubFeed([
        page([row("a", "111", status="active"), row("a", "111", status="complete")], "off-2"),
        page([], None),
    ])
    kept, stats = sweep_buyers(feed, edrpous={"111"}, start_offset="off-1")

    assert len(kept) == 1
    assert kept["a"].status == "complete", "the later row in the feed is the newer state"
    assert stats.unique_tenders == 1


def test_per_buyer_counts_match_the_kept_documents():
    feed = StubFeed([
        page([row("a", "111"), row("b", "111"), row("c", "222")], "off-2"),
        page([], None),
    ])
    kept, stats = sweep_buyers(feed, edrpous={"111", "222"}, start_offset="off-1")

    assert stats.per_buyer == {"111": 2, "222": 1}
    assert sum(stats.per_buyer.values()) == len(kept)


def test_measurement_pass_counts_distinct_tenders_per_buyer():
    feed = StubFeed([
        page([row("a", "111"), row("a", "111"), row("b", "222")], "off-2"),
        page([], None),
    ])
    counts, _ = count_buyers(feed, start_offset="off-1")

    assert counts["111"][1] == 1, "the same tender modified twice is one tender"
    assert counts["222"][1] == 1


def test_document_fetch_failure_is_recorded_not_swallowed():
    feed = StubFeed([page([], None)], documents={"a": {"id": "a"}})
    documents, failures = fetch_documents(feed, ["a", "missing"])

    assert set(documents) == {"a"}
    assert [f["uuid"] for f in failures] == ["missing"]


def test_manifest_counts_match_what_was_written(tmp_path):
    stats = SweepStats(requests=3, rows_seen=10, rows_matched=2, unique_tenders=2, per_buyer={"111": 2})
    manifest = write_dataset(
        tmp_path,
        {"a": {"id": "a"}, "b": {"id": "b"}},
        stats=stats,
        failures=[{"uuid": "c", "error": "NotFound", "message": "gone"}],
        window={"days": 1},
        api_host="example.test",
        elapsed_seconds=12.34,
        swept_at="2026-08-19T00:00:00+00:00",
    )

    written = sorted(p.name for p in (tmp_path / "tenders").glob("*.json.gz"))
    assert written == ["a.json.gz", "b.json.gz"]
    assert manifest["documents_written"] == len(written)
    assert manifest["document_requests"] == 3, "successes plus failures"
    assert manifest["failures"][0]["uuid"] == "c"
    assert json.loads((tmp_path / "manifest.json").read_text())["feed_requests"] == 3


def test_documents_are_stored_raw(tmp_path):
    document = {"id": "a", "procuringEntity": {"identifier": {"id": "111"}}, "extra": [1, 2, 3]}
    write_dataset(
        tmp_path,
        {"a": document},
        stats=SweepStats(),
        failures=[],
        window={},
        api_host="example.test",
        elapsed_seconds=0.0,
    )

    stored = json.loads(gzip.decompress((tmp_path / "tenders" / "a.json.gz").read_bytes()))
    assert stored == document, "the harvest must not pre-normalise; parsing happens on read"


def test_truncation_is_recorded_in_the_manifest(tmp_path):
    manifest = write_dataset(
        tmp_path,
        {"a": {"id": "a"}},
        stats=SweepStats(unique_tenders=50),
        failures=[],
        window={},
        api_host="example.test",
        elapsed_seconds=0.0,
        truncated_to=1,
    )

    assert manifest["documents_truncated_to"] == 1
    assert manifest["matching_tenders_available"] == 50
