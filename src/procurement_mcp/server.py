"""The custom MCP server: four procurement tools over the prepared dataset.

Runs as its own process, spoken to over stdio. It shares no memory with the
agent: everything crosses the MCP boundary as JSON.

    python -m procurement_mcp.server
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any, Callable

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import Configuration
from .errors import ErrorCode, ToolError
from .http import ProzorroClient, ReplayClient
from .store import DatasetStore
from .tools import compliance, concentration, find, screen

SERVER_NAME = "procurement-screening"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = (
    "Tools for screening Ukrainian public procurement records held in a prepared "
    "Prozorro dataset. Typical order: find_tenders to select records, "
    "compute_buyer_supplier_concentration once per buyer for context, then "
    "check_procedure_threshold_compliance and screen_tender_red_flags per tender. "
    "Every tool distinguishes a failure from an empty result: failures carry an "
    "error object with a code, empty results carry result_count 0."
)

logger = logging.getLogger(SERVER_NAME)

ToolRunner = Callable[[Configuration, DatasetStore, dict[str, Any]], dict[str, Any]]

TOOLS: dict[str, tuple[Any, ToolRunner]] = {
    "find_tenders": (find, find.run),
    "compute_buyer_supplier_concentration": (concentration, concentration.run),
    "check_procedure_threshold_compliance": (compliance, compliance.run),
    "screen_tender_red_flags": (screen, screen.run),
}


class ToolHost:
    """Holds configuration and the dataset, loading the dataset on first use."""

    def __init__(self, config: Configuration | None = None) -> None:
        self._config = config
        self._store: DatasetStore | None = None
        # Tool calls run on worker threads and a lookup outside the dataset
        # mutates the store, so access is serialised.
        self._lock = threading.Lock()

    @property
    def config(self) -> Configuration:
        if self._config is None:
            self._config = Configuration.load()
        return self._config

    def _client(self):
        """Replay from recorded responses, or call the API. Never both."""
        settings = self.config.settings
        if settings.offline:
            return ReplayClient(settings.fixture_dir)
        return ProzorroClient(
            requests_per_second=settings.rate_limit_rps, timeout=settings.timeout_seconds
        )

    @property
    def store(self) -> DatasetStore:
        if self._store is None:
            store = DatasetStore(self.config.settings.data_dir, client=self._client())
            store.load()
            if store.skipped:
                logger.warning("skipped %d unreadable dataset documents", len(store.skipped))
            self._store = store
        return self._store

    def describe(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                title=name.replace("_", " ").title(),
                description=module.DESCRIPTION,
                input_schema=module.INPUT_SCHEMA,
                output_schema=module.OUTPUT_SCHEMA,
            )
            for name, (module, _) in TOOLS.items()
        ]

    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        entry = TOOLS.get(name)
        if entry is None:
            raise ToolError(
                ErrorCode.INVALID_INPUT,
                f"unknown tool {name!r}",
                {"available_tools": sorted(TOOLS)},
            )
        _, runner = entry
        with self._lock:
            return runner(self.config, self.store, arguments or {})


def _summarise(payload: dict[str, Any]) -> str:
    """One readable line for the text part.

    The full object travels as structured content; repeating it verbatim as text
    would send every response twice.
    """
    if payload.get("status") == "error":
        error = payload.get("error") or {}
        return f"error {error.get('code')}: {error.get('message')}"
    parts = []
    for key in ("result_count", "total_matched", "risk_score", "has_blocking", "compliant"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    return "ok" + (" (" + ", ".join(parts) + ")" if parts else "")


def _result(payload: dict[str, Any], *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=_summarise(payload))],
        structured_content=payload,
        is_error=is_error,
    )


def build_server(host: ToolHost | None = None) -> Server:
    host = host or ToolHost()

    async def on_list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=host.describe())

    async def on_call_tool(context, params: types.CallToolRequestParams) -> types.CallToolResult:
        try:
            payload = await anyio.to_thread.run_sync(lambda: host.call(params.name, params.arguments))
        except ToolError as exc:
            # A failure is reported as an error result carrying a code, never as
            # an empty success. The caller can branch on error.code.
            logger.info("tool %s failed: %s", params.name, exc.code)
            return _result(exc.to_payload(), is_error=True)
        except Exception as exc:  # never leak a traceback across the boundary
            logger.exception("tool %s raised", params.name)
            return _result(
                {
                    "status": "error",
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": f"{exc.__class__.__name__}: {exc}",
                        "retryable": False,
                        "details": {"tool": params.name},
                    },
                },
                is_error=True,
            )
        return _result(payload)

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("starting %s %s", SERVER_NAME, SERVER_VERSION)
    anyio.run(serve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
