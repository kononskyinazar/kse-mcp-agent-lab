"""Reading and writing the Obsidian demonstration vault through MCP.

The vault is where the run-to-run state lives: the watchlist says which buyers
to screen and which tenders were already judged, and the findings notes are the
output. Both sides go through the approved existing MCP server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import yaml

from .mcp_client import MCPConnection, MCPToolFailure

WATCHLIST_PATH = "procurement/watchlist.md"
FINDINGS_DIR = "procurement/findings"

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Watchlist:
    path: str
    raw: str
    buyers: list[dict[str, Any]] = field(default_factory=list)
    reviewed_tender_ids: list[str] = field(default_factory=list)
    last_reviewed_date: str | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER.match(text or "")
    if not match:
        return {}, text or ""
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        data = {}
    return (data if isinstance(data, dict) else {}), (text[match.end() :])


class ObsidianVault:
    """Thin, explicit wrapper over whichever tool names the bridge exposes."""

    def __init__(self, connection: MCPConnection) -> None:
        self.connection = connection
        self.get_file = connection.find_tool("get", "file", "contents") or connection.find_tool("get", "file")
        self.list_files = connection.find_tool("list", "files", "dir") or connection.find_tool("list", "files")
        # The bridge exposes no whole-file write: content is either appended to
        # a file (creating it when absent) or patched relative to a heading,
        # block or frontmatter field. Both paths are used, for different jobs.
        self.append = connection.find_tool("append", "content")
        self.patch = connection.find_tool("patch", "content")

    def capability_report(self) -> dict[str, str | None]:
        return {
            "get_file": self.get_file,
            "list_files": self.list_files,
            "append": self.append,
            "patch": self.patch,
            "discovered_tools": ", ".join(self.connection.tool_names()),
        }

    def require(self, attribute: str) -> str:
        name = getattr(self, attribute)
        if name is None:
            raise MCPToolFailure(
                self.connection.name,
                attribute,
                {
                    "error": {
                        "code": "TOOL_NOT_AVAILABLE",
                        "message": (
                            f"the Obsidian server exposes no tool matching {attribute!r}; "
                            f"it offers: {', '.join(self.connection.tool_names())}"
                        ),
                    }
                },
            )
        return name

    @staticmethod
    def _as_text(payload: dict[str, Any]) -> str:
        for key in ("text", "content", "value", "result"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return ""

    async def read_note(self, path: str) -> str:
        payload = await self.connection.call(self.require("get_file"), {"filepath": path})
        return self._as_text(payload)

    async def list_notes(self, directory: str) -> list[str]:
        payload = await self.connection.call(self.require("list_files"), {"dirpath": directory})
        for key in ("files", "items", "value", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [str(v) for v in value]
        text = self._as_text(payload)
        return [line.strip() for line in text.splitlines() if line.strip()]

    async def append_note(self, path: str, content: str) -> None:
        await self.connection.call(self.require("append"), {"filepath": path, "content": content})

    async def note_exists(self, path: str, marker: str | None = None) -> bool:
        """Whether the note is already there, optionally carrying a marker."""
        try:
            existing = await self.read_note(path)
        except MCPToolFailure as failure:
            if failure.code in {"NOT_FOUND", "404"}:
                return False
            raise
        if not existing.strip():
            return False
        return marker in existing if marker else True

    async def create_note(self, path: str, content: str, *, marker: str | None = None) -> str:
        """Create a note, or leave it alone if the same finding is already there.

        Appending is the only creation path the bridge offers, so writing twice
        would duplicate the note. The marker - the finding id, derived from the
        run inputs - makes a re-run after a mid-write failure a no-op instead.
        """
        if await self.note_exists(path, marker):
            return "already_present"
        await self.append_note(path, content)
        return "created"

    async def patch_frontmatter(self, path: str, field: str, value: Any) -> None:
        """Replace one frontmatter field. Used for the run-to-run state."""
        import json as _json

        payload = value if isinstance(value, str) else _json.dumps(value, ensure_ascii=False)
        await self.connection.call(
            self.require("patch"),
            {
                "filepath": path,
                "operation": "replace",
                "target_type": "frontmatter",
                "target": field,
                "content": payload,
            },
        )

    async def read_watchlist(self, path: str = WATCHLIST_PATH) -> Watchlist:
        raw = await self.read_note(path)
        frontmatter, _ = parse_frontmatter(raw)
        buyers = frontmatter.get("buyers") or []
        return Watchlist(
            path=path,
            raw=raw,
            buyers=[b for b in buyers if isinstance(b, dict)],
            reviewed_tender_ids=[str(t) for t in (frontmatter.get("reviewed_tender_ids") or [])],
            last_reviewed_date=frontmatter.get("last_reviewed_date"),
            frontmatter=frontmatter,
        )

    async def read_prior_findings(self, directory: str = FINDINGS_DIR) -> list[dict[str, Any]]:
        """Prior conclusions, so the next run does not re-judge what it judged."""
        try:
            names = await self.list_notes(directory)
        except MCPToolFailure as failure:
            if failure.code in {"NOT_FOUND", "404"}:
                return []
            raise

        findings: list[dict[str, Any]] = []
        for name in names:
            if not name.endswith(".md"):
                continue
            path = name if "/" in name else f"{directory}/{name}"
            frontmatter, _ = parse_frontmatter(await self.read_note(path))
            if frontmatter:
                findings.append({**frontmatter, "note_path": path})
        return findings


def findings_path(buyer_edrpou: str, when: date, directory: str = FINDINGS_DIR) -> str:
    return f"{directory}/findings_{buyer_edrpou}_{when.isoformat()}.md"


def finding_id(run_inputs: str) -> str:
    """Idempotent id derived from the run's inputs.

    A re-run after a mid-write failure rewrites the same note instead of
    appending a duplicate.
    """
    import hashlib

    return hashlib.sha256(run_inputs.encode("utf-8")).hexdigest()[:12]
