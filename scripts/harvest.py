#!/usr/bin/env python3
"""Build or measure the prepared procurement dataset.

    python scripts/harvest.py measure --hours 24
    python scripts/harvest.py sweep --days 30

`measure` sizes the production window from observation: it walks a slice of the
feed and reports which buyers are active and how heavily, plus the wall-clock
cost of the walk. `sweep` then harvests the buyers listed in the watchlist.

Both respect one request per second and back off on 429/503.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from procurement_mcp.normalize import parse_datetime  # noqa: E402
from procurement_mcp.harvest import (  # noqa: E402
    count_buyers,
    fetch_documents,
    offset_for,
    sweep_buyers,
    write_dataset,
)
from procurement_mcp.http import DEFAULT_BASE_URL, ProzorroClient  # noqa: E402


def load_watchlist(path: Path) -> tuple[set[str], dict[str, str]]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    buyers = config.get("buyers") or []
    edrpous = {str(b["edrpou"]) for b in buyers}
    names = {str(b["edrpou"]): b.get("name", "") for b in buyers}
    return edrpous, names


def select_documents(kept, *, max_per_buyer: int) -> list[str]:
    """Bound the fetch per buyer, keeping the most recently modified tenders.

    Capping per buyer rather than globally keeps the concentration comparison
    balanced; capping at all is recorded in the manifest.
    """
    # Timestamps are parsed rather than compared as strings: Prozorro returns
    # +02:00 in winter and +03:00 in summer, so lexicographic order stops being
    # chronological order across the change.
    epoch = datetime.min.replace(tzinfo=UTC)
    by_buyer: dict[str, list] = {}
    for uuid, row in kept.items():
        moment = parse_datetime(row.date_modified) or epoch
        by_buyer.setdefault(row.buyer_edrpou or "", []).append((moment, uuid))

    selected: list[str] = []
    for rows in by_buyer.values():
        rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected.extend(uuid for _, uuid in rows[:max_per_buyer])
    return sorted(selected)


def cmd_measure(args: argparse.Namespace) -> int:
    start = datetime.now(UTC) - timedelta(hours=args.hours)
    began = time.monotonic()
    with ProzorroClient(requests_per_second=args.rps) as client:
        counts, stats = count_buyers(
            client, start_offset=offset_for(start), max_requests=args.max_requests
        )
    elapsed = time.monotonic() - began

    ranked = sorted(counts.items(), key=lambda kv: kv[1][1], reverse=True)[: args.top]
    print(
        f"window {args.hours}h | requests {stats.requests} | rows {stats.rows_seen} "
        f"| distinct tenders {stats.unique_tenders} | {elapsed:.0f}s | {stats.stopped_because}"
    )
    truncated = stats.stopped_because == "max_requests_reached"
    if stats.rows_seen and not truncated:
        per_hour = stats.rows_seen / max(args.hours, 1)
        print(f"observed feed volume: ~{per_hour * 24:,.0f} rows/day")
    elif truncated:
        # Dividing a truncated slice by the requested window overstates the rate.
        # This exact mistake put the first estimate at twice the real figure.
        print(
            f"feed volume NOT estimated: the walk stopped at the {args.max_requests}-request "
            f"cap before covering {args.hours}h, so rows/day cannot be inferred from it. "
            f"Re-run with --max-requests above {stats.requests} for a usable figure."
        )
    print(f"\ntop {args.top} buyers by distinct tenders in this slice:")
    for edrpou, (name, count) in ranked:
        print(f"  {edrpou:<12} {count:>5}  {name[:70]}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "hours": args.hours,
                    "requests": stats.requests,
                    "rows_seen": stats.rows_seen,
                    "distinct_tenders": stats.unique_tenders,
                    "elapsed_seconds": round(elapsed, 1),
                    "stopped_because": stats.stopped_because,
                    "covered_the_requested_window": not truncated,
                    "rows_per_day_estimate": (
                        round(stats.rows_seen / max(args.hours, 1) * 24) if not truncated else None
                    ),
                    "buyers": [
                        {"edrpou": e, "name": n, "tenders": c} for e, (n, c) in ranked
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    edrpous, names = load_watchlist(Path(args.watchlist))
    if not edrpous:
        print("watchlist is empty; nothing to sweep", file=sys.stderr)
        return 2

    start = datetime.now(UTC) - timedelta(days=args.days)
    began = time.monotonic()
    with ProzorroClient(requests_per_second=args.rps) as client:
        kept, stats = sweep_buyers(
            client,
            edrpous=edrpous,
            start_offset=offset_for(start),
            max_requests=args.max_requests,
        )
        print(
            f"feed: {stats.requests} requests, {stats.rows_seen} rows, "
            f"{stats.unique_tenders} matching tenders ({stats.stopped_because})",
            file=sys.stderr,
        )
        selected = select_documents(kept, max_per_buyer=args.max_per_buyer)
        if len(selected) < len(kept):
            print(
                f"capping at {args.max_per_buyer} tenders per buyer: "
                f"{len(selected)} of {len(kept)} matching tenders will be fetched",
                file=sys.stderr,
            )
        documents, failures = fetch_documents(client, selected)
    elapsed = time.monotonic() - began

    manifest = write_dataset(
        Path(args.out),
        documents,
        stats=stats,
        failures=failures,
        window={
            "days": args.days,
            "basis": "dateModified",
            "from": start.isoformat(timespec="seconds"),
            "to": datetime.now(UTC).isoformat(timespec="seconds"),
        },
        api_host=DEFAULT_BASE_URL,
        elapsed_seconds=elapsed,
        truncated_to=args.max_per_buyer if len(selected) < len(kept) else None,
    )
    print(json.dumps({**manifest, "buyer_names": names}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rps", type=float, default=1.0, help="requests per second (default 1)")
    sub = parser.add_subparsers(dest="command", required=True)

    measure = sub.add_parser("measure", help="size the window from observation")
    measure.add_argument("--hours", type=int, default=24)
    measure.add_argument("--max-requests", type=int, default=60)
    measure.add_argument("--top", type=int, default=25)
    measure.add_argument("--out", default=None)
    measure.set_defaults(func=cmd_measure)

    sweep = sub.add_parser("sweep", help="harvest the watchlist buyers")
    sweep.add_argument("--days", type=int, default=30)
    sweep.add_argument("--max-requests", type=int, default=6000)
    sweep.add_argument("--watchlist", default=str(ROOT / "config" / "watchlist.yaml"))
    sweep.add_argument("--max-per-buyer", type=int, default=150)
    sweep.add_argument("--out", default=str(ROOT / "data"))
    sweep.set_defaults(func=cmd_sweep)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
