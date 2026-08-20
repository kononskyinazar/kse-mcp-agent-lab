"""The prepared dataset: load raw documents, normalise on read, index.

Every tool reads through this object, so the data window it reports is the same
one the tools quote back to the caller. A short window is stated, never hidden.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ErrorCode, ToolError, data_integrity, not_found
from .models import Tender
from .normalize import normalize_tender


@dataclass(frozen=True)
class DataWindow:
    source: str
    swept_at: str | None
    window_days: int | None
    basis: str | None
    tender_count: int
    earliest_publication: str | None
    latest_publication: str | None
    truncated_to: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "swept_at": self.swept_at,
            "window_days": self.window_days,
            "window_basis": self.basis,
            "tenders_in_dataset": self.tender_count,
            "earliest_publication": self.earliest_publication,
            "latest_publication": self.latest_publication,
            "documents_truncated_to_per_buyer": self.truncated_to,
        }


def _read_document(path: Path) -> dict[str, Any]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return json.loads(raw)


UUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class DatasetStore:
    """The prepared dataset, optionally backed by a live or replayed fetch.

    A tender absent from the dataset can still be resolved when a client is
    supplied: live mode calls the API, replay mode serves a recorded response.
    Either way the document goes through the same normalisation as a stored one,
    so a fixture cannot short-circuit the parsing path.
    """

    def __init__(self, directory: Path, client: Any | None = None) -> None:
        self.directory = Path(directory)
        self.client = client
        # Tenders fetched from outside the prepared dataset are kept apart from
        # it. Indexing them would let a replay or live lookup change the
        # concentration, streak and retrieval answers given afterwards in the
        # same process, so the same call would depend on what preceded it.
        self._external: dict[str, Tender] = {}
        self._tenders: dict[str, Tender] = {}
        self._by_tender_id: dict[str, str] = {}
        self._by_buyer: dict[str, list[str]] = defaultdict(list)
        self._by_supplier: dict[str, list[str]] = defaultdict(list)
        self._skipped: list[dict[str, str]] = []
        self._manifest: dict[str, Any] = {}
        self._loaded = False

    # -- loading ---------------------------------------------------------

    def load(self) -> "DatasetStore":
        if self._loaded:
            return self
        tenders_dir = self.directory / "tenders"
        if not tenders_dir.is_dir():
            raise ToolError(
                ErrorCode.DATA_INTEGRITY,
                f"no prepared dataset at {tenders_dir}; run scripts/harvest.py sweep first",
                {"expected_directory": str(tenders_dir)},
            )

        manifest_path = self.directory / "manifest.json"
        if manifest_path.exists():
            self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for path in sorted([*tenders_dir.glob("*.json"), *tenders_dir.glob("*.json.gz")]):
            try:
                tender = normalize_tender(_read_document(path))
            except ToolError as exc:
                # One unusable document must not cost the whole dataset; the
                # skip is counted and reportable rather than silent.
                self._skipped.append({"file": path.name, "reason": exc.message})
                continue
            except (OSError, ValueError) as exc:
                self._skipped.append({"file": path.name, "reason": f"unreadable: {exc}"})
                continue
            self._index(tender)

        self._loaded = True
        self._verify_against_manifest()
        return self

    def _verify_against_manifest(self) -> None:
        """The manifest and the directory must agree, or neither can be trusted."""
        expected = self._manifest.get("documents_written")
        if expected is None:
            return
        actual = len(self._tenders) + len(self._skipped)
        if actual != expected:
            raise ToolError(
                ErrorCode.DATA_INTEGRITY,
                f"the dataset holds {actual} documents but the manifest records {expected}; "
                f"the directory probably contains documents from more than one sweep",
                {"documents_found": actual, "documents_in_manifest": expected,
                 "directory": str(self.directory / "tenders")},
            )

    def _index(self, tender: Tender) -> None:
        self._tenders[tender.uuid] = tender
        if tender.tender_id:
            self._by_tender_id[tender.tender_id] = tender.uuid
        if tender.buyer.edrpou:
            self._by_buyer[tender.buyer.edrpou].append(tender.uuid)
        for award in tender.awards:
            if not award.is_active:
                continue
            for edrpou in award.supplier_edrpous:
                self._by_supplier[edrpou].append(tender.uuid)

    # -- access ----------------------------------------------------------

    @property
    def skipped(self) -> list[dict[str, str]]:
        return list(self._skipped)

    def __len__(self) -> int:
        return len(self._tenders)

    def all_tenders(self) -> list[Tender]:
        return list(self._tenders.values())

    def get(self, identifier: str) -> Tender:
        """Look up by document UUID or by the human-facing tenderID."""
        if identifier in self._tenders:
            return self._tenders[identifier]
        uuid = self._by_tender_id.get(identifier)
        if uuid is not None:
            return self._tenders[uuid]
        if identifier in self._external:
            return self._external[identifier]

        if self.client is not None and UUID_PATTERN.match(identifier):
            return self._fetch(identifier)

        raise not_found(
            f"tender {identifier!r} is not in the prepared dataset",
            identifier=identifier,
            dataset_size=len(self._tenders),
            hint=(
                "the human-facing tenderID cannot be resolved upstream; supply the "
                "32-character document UUID to fetch a tender outside the dataset"
            ),
        )

    def _fetch(self, uuid: str) -> Tender:
        payload = self.client.get_json(f"/tenders/{uuid}")
        document = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(document, dict):
            raise data_integrity("upstream response carries no tender document", uuid=uuid)
        tender = normalize_tender(document)
        self._external[tender.uuid] = tender
        return tender

    def was_fetched(self, uuid: str) -> bool:
        return uuid in self._external

    def source_of(self, uuid: str) -> str:
        """Where a tender came from, so a reader can trace it."""
        if uuid in self._tenders:
            return "prepared_dataset"
        if uuid in self._external:
            return "offline_replay" if type(self.client).__name__ == "ReplayClient" else "live_api"
        return "unknown"

    def for_buyer(self, edrpou: str) -> list[Tender]:
        return [self._tenders[uuid] for uuid in self._by_buyer.get(edrpou, ())]

    def for_supplier(self, edrpou: str) -> list[Tender]:
        return [self._tenders[uuid] for uuid in self._by_supplier.get(edrpou, ())]

    def known_buyers(self) -> list[str]:
        return sorted(self._by_buyer)

    def supplier_first_seen(self, edrpou: str) -> datetime | None:
        """Earliest publication date at which this supplier won anything here.

        This is a dataset-horizon proxy, not a company registration date: the
        API does not publish registration dates. Callers must present it as
        such.
        """
        dates = [t.published_at for t in self.for_supplier(edrpou) if t.published_at]
        return min(dates) if dates else None

    def data_window(self) -> DataWindow:
        published = sorted(t.published_at for t in self._tenders.values() if t.published_at)
        window = self._manifest.get("window") or {}
        return DataWindow(
            source=self._manifest.get("api_host", "prepared dataset"),
            swept_at=self._manifest.get("swept_at"),
            window_days=window.get("days"),
            basis=window.get("basis"),
            tender_count=len(self._tenders),
            earliest_publication=published[0].isoformat() if published else None,
            latest_publication=published[-1].isoformat() if published else None,
            truncated_to=self._manifest.get("documents_truncated_to"),
        )


def require_edrpou(value: Any, *, field: str = "buyer_edrpou") -> str:
    """EDRPOU is 8 digits, or 10 for some registrations. Reject anything else.

    A malformed code is INVALID_INPUT; a well-formed code with no tenders is a
    successful empty result. Keeping the two apart is the whole point.
    """
    from .errors import invalid_input

    if not isinstance(value, str):
        raise invalid_input(f"{field} must be a string of digits", field=field, received=type(value).__name__)
    code = value.strip()
    if not code.isdigit() or len(code) not in (8, 10):
        raise invalid_input(
            f"{field} must be an 8- or 10-digit EDRPOU code",
            field=field,
            received=value,
        )
    return code


def coerce_date(value: Any, *, field: str) -> datetime | None:
    from .errors import invalid_input

    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise invalid_input(f"{field} must be an ISO date string", field=field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise invalid_input(f"{field} is not an ISO date: {value!r}", field=field) from exc
    return parsed


def ensure_dataset(store: DatasetStore) -> DatasetStore:
    if len(store.load()) == 0:
        raise data_integrity(
            "the prepared dataset is empty; run scripts/harvest.py sweep",
            directory=str(store.directory),
        )
    return store
