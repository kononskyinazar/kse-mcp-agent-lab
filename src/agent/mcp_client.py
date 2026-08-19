"""The agent's MCP client: one connection per configured server.

Kept deliberately thin and explicit. Both servers are reached the same way -
launch the process, initialise the session, discover its tools, call them - so
the boundary between agent, MCP client, server and data source is visible in
one file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPConnectionError(RuntimeError):
    """The server could not be started or the session could not be initialised."""

    def __init__(self, server: str, message: str) -> None:
        super().__init__(f"MCP server {server!r} is unavailable: {message}")
        self.server = server
        self.message = message


class MCPToolFailure(RuntimeError):
    """The server answered, and the answer was a failure."""

    def __init__(self, server: str, tool: str, payload: dict[str, Any]) -> None:
        error = (payload or {}).get("error") or {}
        self.code = error.get("code", "UNKNOWN")
        self.detail = error.get("message", "no message")
        self.payload = payload
        self.server = server
        self.tool = tool
        super().__init__(f"{server}.{tool} failed [{self.code}]: {self.detail}")


def expand(value: str) -> str:
    """Expand ${VAR} and ${VAR:-default} from the environment.

    Defaults matter here: the interpreter that runs the custom server differs
    between machines, and a configuration file that only works on one laptop is
    not reproducible.
    """
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        return os.environ.get(name) or (default or "")

    return pattern.sub(replace, value)


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def resolved_env(self) -> dict[str, str]:
        """Expand ${VAR} from the process environment; missing values stay empty.

        Secrets therefore live only in the environment, never in the committed
        configuration file.
        """
        return {
            key: expand(value) if isinstance(value, str) else str(value)
            for key, value in self.env.items()
        }


def load_server_specs(path: Path) -> dict[str, ServerSpec]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    servers = payload.get("mcpServers") or {}
    return {
        name: ServerSpec(
            name=name,
            command=expand(spec["command"]),
            args=[expand(a) if isinstance(a, str) else a for a in spec.get("args") or []],
            env=dict(spec.get("env") or {}),
            description=spec.get("description", ""),
        )
        for name, spec in servers.items()
    }


class MCPConnection:
    """One live MCP session."""

    def __init__(self, spec: ServerSpec) -> None:
        self.spec = spec
        self.tools: list[types.Tool] = []
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    @property
    def name(self) -> str:
        return self.spec.name

    async def __aenter__(self) -> "MCPConnection":
        if shutil.which(self.spec.command) is None and not Path(self.spec.command).exists():
            raise MCPConnectionError(self.name, f"command {self.spec.command!r} not found on PATH")

        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(
                command=self.spec.command,
                args=self.spec.args,
                env={**os.environ, **self.spec.resolved_env()},
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
        except MCPConnectionError:
            await stack.aclose()
            raise
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(self.name, f"{exc.__class__.__name__}: {exc}") from exc

        self._stack = stack
        self._session = session
        self.tools = list(listed.tools)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    def find_tool(self, *fragments: str) -> str | None:
        """Locate a tool by name fragment.

        The Obsidian bridge names its tools differently between versions, so the
        agent matches on a fragment and reports what it actually found rather
        than assuming a name.
        """
        for tool in self.tools:
            lowered = tool.name.lower()
            if all(fragment.lower() in lowered for fragment in fragments):
                return tool.name
        return None

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._session is None:
            raise MCPConnectionError(self.name, "session is not open")
        try:
            result = await self._session.call_tool(tool, arguments or {})
        except Exception as exc:
            raise MCPConnectionError(self.name, f"call to {tool!r} failed: {exc}") from exc

        payload = result.structured_content
        if payload is None:
            text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
            try:
                payload = json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                payload = {"status": "ok", "text": text}

        if result.is_error:
            raise MCPToolFailure(self.name, tool, payload if isinstance(payload, dict) else {})
        return payload if isinstance(payload, dict) else {"status": "ok", "value": payload}


class MCPHub:
    """Opens several connections and closes them together."""

    def __init__(self, specs: dict[str, ServerSpec]) -> None:
        self.specs = specs
        self.connections: dict[str, MCPConnection] = {}
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> "MCPHub":
        await self._stack.__aenter__()
        for name, spec in self.specs.items():
            connection = await self._stack.enter_async_context(MCPConnection(spec))
            self.connections[name] = connection
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()
        self.connections.clear()

    def __getitem__(self, name: str) -> MCPConnection:
        return self.connections[name]

    def discovery_report(self) -> dict[str, list[str]]:
        return {name: conn.tool_names() for name, conn in self.connections.items()}
