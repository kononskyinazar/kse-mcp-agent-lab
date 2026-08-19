#!/usr/bin/env python3
"""Record genuine API responses for offline replay.

    python scripts/record_fixtures.py <document-uuid> [<document-uuid> ...]
    python scripts/record_fixtures.py --recent 2

Fixtures are the bytes the API returned, saved under the same key the client
derives at call time. Replay therefore goes through the ordinary parsing path;
nothing here short-circuits it with a prepared answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from procurement_mcp.http import DEFAULT_BASE_URL, ProzorroClient, fixture_key  # noqa: E402


def record(client: ProzorroClient, path: str, params: dict | None, out_dir: Path) -> Path:
    payload = client.get_json(path, params)
    target = out_dir / fixture_key(path, params)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("uuids", nargs="*", help="document UUIDs to record")
    parser.add_argument("--recent", type=int, default=0, help="also record N recent tenders from the feed")
    parser.add_argument("--out", default=str(ROOT / "fixtures"))
    parser.add_argument("--rps", type=float, default=1.0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    with ProzorroClient(requests_per_second=args.rps) as client:
        uuids = list(args.uuids)
        if args.recent:
            params = {"descending": 1, "limit": args.recent, "opt_fields": "tenderID,status"}
            written.append(record(client, "/tenders", params, out_dir))
            feed = json.loads(written[-1].read_text(encoding="utf-8"))
            uuids.extend(row["id"] for row in feed.get("data", []))

        for uuid in uuids:
            written.append(record(client, f"/tenders/{uuid}", None, out_dir))

    manifest = {
        "recorded_from": DEFAULT_BASE_URL,
        "files": [p.name for p in written],
        "note": (
            "Responses are stored verbatim. Replay mode serves them through the same "
            "parsing path as a live call; there is no branch that returns a prepared answer."
        ),
    }
    (out_dir / "README.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for path in written:
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
