"""Helpers that build a real store and a real configuration for tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from procurement_mcp.config import ROOT, Configuration, RuleBook, Settings
from procurement_mcp.harvest import SweepStats, write_dataset
from procurement_mcp.store import DatasetStore
from procurement_mcp.thresholds import StatutoryBook

CONFIG_DIR = ROOT / "config"


def make_store(directory: Path, documents: Iterable[dict[str, Any]]) -> DatasetStore:
    docs = {d["id"]: d for d in documents}
    write_dataset(
        directory,
        docs,
        stats=SweepStats(unique_tenders=len(docs)),
        failures=[],
        window={"days": 30, "basis": "dateModified"},
        api_host="test",
        elapsed_seconds=0.0,
        swept_at="2026-08-19T00:00:00+00:00",
    )
    return DatasetStore(directory).load()


def make_config(directory: Path) -> Configuration:
    settings = Settings(
        data_dir=directory,
        fixture_dir=directory / "fixtures",
        config_dir=CONFIG_DIR,
        offline=True,
        rate_limit_rps=1.0,
        timeout_seconds=5.0,
    )
    return Configuration(
        settings=settings,
        rule_book=RuleBook.from_file(CONFIG_DIR / "rules.yaml"),
        statutes=StatutoryBook.from_file(CONFIG_DIR / "statutory_thresholds.yaml"),
    )


def write_broken_document(directory: Path, name: str, payload: Any) -> None:
    (directory / "tenders").mkdir(parents=True, exist_ok=True)
    (directory / "tenders" / name).write_text(json.dumps(payload), encoding="utf-8")
