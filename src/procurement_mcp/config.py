"""Server settings and the versioned rule and threshold configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import data_integrity
from .thresholds import StatutoryBook

def _project_root() -> Path:
    """Locate the checkout that carries config/ and data/.

    Path(__file__).parents[2] is the repository root only for an editable
    install; installed normally it lands in site-packages, and the declared
    console script would look for rules.yaml there. So: walk up from the module
    and from the working directory looking for the marker, and fall back to the
    working directory, where the environment variables can still point the way.
    """
    marker = Path("config") / "rules.yaml"
    for start in (Path(__file__).resolve(), Path.cwd().resolve() / "_"):
        for candidate in start.parents:
            if (candidate / marker).is_file():
                return candidate
    return Path.cwd().resolve()


ROOT = _project_root()


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    fixture_dir: Path
    config_dir: Path
    offline: bool
    rate_limit_rps: float
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("CUSTOM_MCP_DATA_DIR", ROOT / "data")),
            fixture_dir=Path(os.environ.get("CUSTOM_MCP_FIXTURE_DIR", ROOT / "fixtures")),
            config_dir=Path(os.environ.get("CUSTOM_MCP_CONFIG_DIR", ROOT / "config")),
            offline=_flag("CUSTOM_MCP_OFFLINE", True),
            rate_limit_rps=float(os.environ.get("CUSTOM_MCP_RATE_LIMIT_RPS", "1")),
            timeout_seconds=float(os.environ.get("CUSTOM_MCP_TIMEOUT_SECONDS", "15")),
        )


@dataclass(frozen=True)
class RuleBook:
    version: str
    scoring: dict[str, Any]
    rules: dict[str, Any]

    @classmethod
    def from_file(cls, path: Path) -> "RuleBook":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        rules = payload.get("rules")
        if not rules:
            raise data_integrity(f"rule configuration at {path} defines no rules")
        return cls(
            version=str(payload.get("version", "unversioned")),
            scoring=payload.get("scoring") or {},
            rules=rules,
        )

    @property
    def human_review_threshold(self) -> float:
        return float(self.scoring.get("human_review_threshold", 60))


@dataclass(frozen=True)
class Configuration:
    settings: Settings
    rule_book: RuleBook
    statutes: StatutoryBook

    @classmethod
    def load(cls, settings: Settings | None = None) -> "Configuration":
        settings = settings or Settings.from_env()
        return cls(
            settings=settings,
            rule_book=RuleBook.from_file(settings.config_dir / "rules.yaml"),
            statutes=StatutoryBook.from_file(settings.config_dir / "statutory_thresholds.yaml"),
        )

    def provenance(self) -> dict[str, Any]:
        return {
            "rules_version": self.rule_book.version,
            "thresholds_version": self.statutes.version,
            "classifier": self.statutes.classifier,
            "mode": "offline-replay" if self.settings.offline else "live",
            # Published so the agent does not keep a second copy of the policy.
            "human_review_threshold": self.rule_book.human_review_threshold,
        }
