"""Building the prepared dataset from the public change feed.

The feed is the only way to reach a named buyer's tenders: ``tenderID`` cannot
be resolved to a document UUID through any public endpoint, and the search
service returns neither the UUID nor award data. So the sweep walks the feed
**ascending** from a start offset - the resumable direction - keeps rows whose
buyer is on the allow-list, and then fetches those documents in full.

Documents are stored exactly as received. Normalisation happens on read, so the
live path, the replay path and the dataset all traverse the same parsing code
at demonstration time.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

FEED_PATH = "/tenders"
FEED_FIELDS = "tenderID,status,procuringEntity"


class FeedClient(Protocol):
    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass
class FeedRow:
    uuid: str
    tender_id: str | None
    status: str | None
    buyer_edrpou: str | None
    buyer_name: str | None
    date_modified: str | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FeedRow":
        entity = payload.get("procuringEntity") or {}
        identifier = entity.get("identifier") or {}
        return cls(
            uuid=payload.get("id", ""),
            tender_id=payload.get("tenderID"),
            status=payload.get("status"),
            buyer_edrpou=identifier.get("id"),
            buyer_name=identifier.get("legalName") or entity.get("name"),
            date_modified=payload.get("dateModified"),
        )


@dataclass
class SweepStats:
    requests: int = 0
    rows_seen: int = 0
    rows_matched: int = 0
    unique_tenders: int = 0
    per_buyer: dict[str, int] = field(default_factory=dict)
    first_offset: str | None = None
    last_offset: str | None = None
    stopped_because: str = "feed_exhausted"


def offset_for(moment: datetime) -> str:
    """Feed offsets are unix timestamps; ascending paging starts from one."""
    return str(int(moment.timestamp()))


def iter_feed(
    client: FeedClient,
    *,
    start_offset: str,
    limit: int = 1000,
    max_requests: int = 10_000,
    stats: SweepStats | None = None,
) -> Iterator[FeedRow]:
    """Walk the feed forwards, following the server's own cursor.

    Ascending is not a preference. The feed is ordered by ``dateModified`` and
    mutates continuously, so a descending offset stops being a valid cursor as
    soon as anything is modified mid-sweep, and rows are skipped or repeated.
    """
    stats = stats if stats is not None else SweepStats()
    offset = start_offset
    stats.first_offset = offset

    for _ in range(max_requests):
        payload = client.get_json(FEED_PATH, {"offset": offset, "limit": limit, "opt_fields": FEED_FIELDS})
        rows = payload.get("data") or []
        stats.requests += 1
        stats.rows_seen += len(rows)

        for row in rows:
            yield FeedRow.from_payload(row)

        next_offset = ((payload.get("next_page") or {}).get("offset")) or None
        if not rows or next_offset is None or next_offset == offset:
            stats.stopped_because = "feed_exhausted"
            break
        offset = next_offset
        stats.last_offset = offset
    else:
        stats.stopped_because = "max_requests_reached"


def sweep_buyers(
    client: FeedClient,
    *,
    edrpous: set[str],
    start_offset: str,
    limit: int = 1000,
    max_requests: int = 10_000,
) -> tuple[dict[str, FeedRow], SweepStats]:
    """Collect the newest feed row per tender for the buyers on the allow-list."""
    stats = SweepStats()
    kept: dict[str, FeedRow] = {}

    for row in iter_feed(
        client, start_offset=start_offset, limit=limit, max_requests=max_requests, stats=stats
    ):
        if not row.uuid or row.buyer_edrpou not in edrpous:
            continue
        stats.rows_matched += 1
        kept[row.uuid] = row  # later rows are newer, so last write wins

    stats.unique_tenders = len(kept)
    for row in kept.values():
        if row.buyer_edrpou:
            stats.per_buyer[row.buyer_edrpou] = stats.per_buyer.get(row.buyer_edrpou, 0) + 1
    return kept, stats


def count_buyers(
    client: FeedClient, *, start_offset: str, limit: int = 1000, max_requests: int = 60
) -> tuple[dict[str, tuple[str, int]], SweepStats]:
    """Measurement pass: which buyers are active, and how heavily.

    Used to size the production window from observation instead of assuming it.
    """
    stats = SweepStats()
    seen: dict[str, set[str]] = {}
    names: dict[str, str] = {}

    for row in iter_feed(
        client, start_offset=start_offset, limit=limit, max_requests=max_requests, stats=stats
    ):
        if not row.buyer_edrpou or not row.uuid:
            continue
        seen.setdefault(row.buyer_edrpou, set()).add(row.uuid)
        if row.buyer_name:
            names.setdefault(row.buyer_edrpou, row.buyer_name)

    stats.unique_tenders = sum(len(v) for v in seen.values())
    return {edrpou: (names.get(edrpou, ""), len(ids)) for edrpou, ids in seen.items()}, stats


def fetch_documents(
    client: FeedClient, uuids: list[str], *, on_error: str = "skip"
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Fetch full documents. A single bad document must not lose the sweep."""
    documents: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []

    for uuid in uuids:
        try:
            payload = client.get_json(f"{FEED_PATH}/{uuid}")
        except Exception as exc:  # surfaced in the manifest, never silently dropped
            if on_error == "raise":
                raise
            failures.append({"uuid": uuid, "error": exc.__class__.__name__, "message": str(exc)})
            continue
        data = payload.get("data")
        if isinstance(data, dict):
            documents[uuid] = data
        else:
            failures.append({"uuid": uuid, "error": "MalformedDocument", "message": "no data object"})
    return documents, failures


def write_dataset(
    directory: Path,
    documents: dict[str, dict[str, Any]],
    *,
    stats: SweepStats,
    failures: list[dict[str, str]],
    window: dict[str, Any],
    api_host: str,
    elapsed_seconds: float,
    swept_at: str | None = None,
    compress: bool = True,
    truncated_to: int | None = None,
    replace_existing: bool = True,
) -> dict[str, Any]:
    """Write raw documents plus the manifest the design promises.

    Documents are gzipped only to keep the committed dataset small; the bytes
    inside are exactly what the API returned, and they are parsed on read by the
    same code that parses a live response.
    """
    tenders_dir = directory / "tenders"
    tenders_dir.mkdir(parents=True, exist_ok=True)

    # A sweep describes exactly what it fetched. Leaving an earlier sweep's
    # documents in place would make the store load the union of several
    # harvests while the manifest described only the last one, so every window
    # figure the tools quote would be wrong with nothing to reveal it.
    removed = 0
    if replace_existing:
        for stale in [*tenders_dir.glob("*.json"), *tenders_dir.glob("*.json.gz")]:
            stale.unlink()
            removed += 1

    for uuid, document in documents.items():
        blob = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
        if compress:
            (tenders_dir / f"{uuid}.json.gz").write_bytes(gzip.compress(blob, mtime=0))
        else:
            (tenders_dir / f"{uuid}.json").write_bytes(blob)

    manifest = {
        "swept_at": swept_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "api_host": api_host,
        "window": window,
        "offsets": {"first": stats.first_offset, "last": stats.last_offset},
        "feed_requests": stats.requests,
        "document_requests": len(documents) + len(failures),
        "elapsed_seconds": round(elapsed_seconds, 1),
        "rows_seen": stats.rows_seen,
        "rows_matched": stats.rows_matched,
        "documents_written": len(documents),
        "per_buyer": stats.per_buyer,
        "failures": failures,
        "stopped_because": stats.stopped_because,
        "compressed": compress,
        "stale_documents_removed": removed,
        # A bounded harvest must say it was bounded; a silent cap reads as
        # "this is everything" when it is not.
        "documents_truncated_to": truncated_to,
        "matching_tenders_available": stats.unique_tenders,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
