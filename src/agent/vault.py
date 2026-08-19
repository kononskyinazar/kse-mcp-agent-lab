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
RUN_LOG_PATH = "procurement/findings/_run-log.md"

TENDER_ID = re.compile(r"UA-\d{4}-\d{2}-\d{2}-\d{6}-[a-z]")

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
        # The bridge's only usable write is append, which creates the file when
        # it is absent. There is no whole-file write, and patch_content fails on
        # this plugin version for every target type: the bridge does not send the
        # Markdown-Patch-Version header the plugin now requires (error 40084).
        # The run-to-run state is therefore an append-only log, not an edited
        # frontmatter field - which also means the agent can never damage what
        # the analyst wrote.
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

    async def write_finding(
        self, path: str, *, frontmatter: str, body: str, marker: str, heading: str
    ) -> str:
        """Create the note, add a revision block, or do nothing.

        Append is the only write the bridge supports here, so the three cases are
        handled explicitly:

        * note absent - write frontmatter and body;
        * note present without this marker - append a revision block **without**
          a second frontmatter block, because only the first one is read and two
          of them make the note malformed;
        * note present with this marker - the same findings are already recorded,
          so a re-run after a partial failure changes nothing.
        """
        try:
            existing = await self.read_note(path)
        except MCPToolFailure as failure:
            if failure.code not in {"NOT_FOUND", "404"}:
                raise
            existing = ""

        if marker and marker in existing:
            return "already_present"
        if existing.strip():
            await self.append_note(path, f"\n\n---\n\n## {heading}\n\n<!-- {marker} -->\n\n{body}\n")
            return "revised"
        await self.append_note(path, frontmatter + "\n" + body + "\n")
        return "created"

    async def append_run_log(
        self,
        run_id: str,
        when: str,
        *,
        buyers: list[str],
        screened: list[str],
        flagged: list[str],
        decision: str = "written",
        path: str = RUN_LOG_PATH,
    ) -> str:
        """Record what this run looked at, by appending a block to the run log.

        This is the run-to-run memory. It is append-only because that is the only
        write the bridge actually supports here, and the arrangement is better
        than the one it replaced: the analyst's own note is never rewritten by
        the agent.
        """
        block = "\n".join(
            [
                "",
                f"## {run_id} — {when}",
                "",
                f"- decision: {decision}",
                f"- buyers: {', '.join(buyers) or 'none'}",
                f"- screened: {', '.join(screened) or 'none'}",
                f"- flagged: {', '.join(flagged) or 'none'}",
                "",
            ]
        )
        await self.append_note(path, block)
        return path

    async def read_run_log(self, path: str = RUN_LOG_PATH) -> set[str]:
        """Tender ids screened by previous runs, from the append-only log."""
        try:
            text = await self.read_note(path)
        except MCPToolFailure as failure:
            if failure.code in {"NOT_FOUND", "404"}:
                return set()
            raise
        return parse_run_log(text)

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


def parse_run_log(text: str) -> set[str]:
    """Tender ids recorded by previous runs.

    Kept pure and separate from the fetch so it can be tested, and read, without
    a running vault.
    """
    seen: set[str] = set()
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- screened:") or stripped.startswith("- flagged:"):
            seen.update(TENDER_ID.findall(stripped))
    return seen


def findings_path(buyer_edrpou: str, when: date, directory: str = FINDINGS_DIR) -> str:
    return f"{directory}/findings_{buyer_edrpou}_{when.isoformat()}.md"


def finding_id(run_inputs: str) -> str:
    """Idempotent id derived from the run's inputs.

    A re-run after a mid-write failure rewrites the same note instead of
    appending a duplicate.
    """
    import hashlib

    return hashlib.sha256(run_inputs.encode("utf-8")).hexdigest()[:12]
