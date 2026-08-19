"""Run the agent.

    python -m agent.main                 # full run against the configured vault
    python -m agent.main --dry-run       # screen and print, write nothing
    python -m agent.main --check         # connect, list both servers' tools, exit

Both MCP servers are launched from config/mcp.json. The custom server can also
be started by hand first; this process talks to whichever one the configuration
points at.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import anyio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent.graph import AgentDeps, build_graph  # noqa: E402
from agent.mcp_client import MCPConnectionError, MCPHub, load_server_specs  # noqa: E402
from agent.model import ModelUnavailable, build_model  # noqa: E402


def load_env(path: Path) -> None:
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path)
    except ImportError:  # pragma: no cover - dependency guard
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


async def run(args: argparse.Namespace) -> int:
    specs = load_server_specs(Path(args.config))
    if args.only:
        specs = {name: spec for name, spec in specs.items() if name in set(args.only)}

    try:
        async with MCPHub(specs) as hub:
            report = hub.discovery_report()
            print("MCP connections discovered:", file=sys.stderr)
            for name, tools in report.items():
                print(f"  {name}: {len(tools)} tools -> {', '.join(tools)}", file=sys.stderr)
            if args.check:
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return 0

            deps = AgentDeps(
                hub=hub,
                model=build_model(),
                human_review_threshold=args.review_threshold,
                require_approval=not args.no_approval,
            )
            graph = build_graph(deps)

            from langgraph.checkpoint.memory import InMemorySaver

            compiled = graph.compile(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": args.thread}}
            state = await compiled.ainvoke({"watchlist_path": args.watchlist}, config=config)

            interrupts = state.get("__interrupt__")
            if interrupts:
                request = interrupts[0].value
                print(json.dumps(request, indent=2, ensure_ascii=False, default=str))
                if args.dry_run:
                    print("dry run: stopping at the approval gate, nothing written", file=sys.stderr)
                    return 0
                answer = input("Approve writing these findings to the vault? [y/N] ").strip().lower()
                from langgraph.types import Command

                state = await compiled.ainvoke(
                    Command(resume={"approved": answer in {"y", "yes"}}), config=config
                )

            print(
                json.dumps(
                    {
                        "run_id": state.get("run_id"),
                        "buyers": len(state.get("plan") or []),
                        "candidates": len(state.get("candidates") or []),
                        "screened": len(state.get("screened") or []),
                        "flagged": len(state.get("flagged") or []),
                        "notes_written": state.get("written") or [],
                        "errors": state.get("errors") or [],
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
            return 0
    except MCPConnectionError as exc:
        # The failure the defence demonstrates: the existing server is down.
        print(f"\nMCP CONNECTION FAILURE: {exc}", file=sys.stderr)
        print(
            "The run stopped before any write. Check that Obsidian is running with the "
            "Local REST API plugin enabled and that OBSIDIAN_API_KEY matches.",
            file=sys.stderr,
        )
        return 3
    except ModelUnavailable as exc:
        print(f"\nMODEL UNAVAILABLE: {exc}", file=sys.stderr)
        return 4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "config" / "mcp.json"))
    parser.add_argument("--watchlist", default="procurement/watchlist.md")
    parser.add_argument("--thread", default="defence")
    parser.add_argument("--review-threshold", type=float, default=60.0)
    parser.add_argument("--no-approval", action="store_true", help="skip the human approval gate")
    parser.add_argument("--dry-run", action="store_true", help="stop at the approval gate")
    parser.add_argument("--check", action="store_true", help="connect, list tools, exit")
    parser.add_argument("--only", nargs="*", help="connect to a subset of servers, by name")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    return anyio.run(lambda: run(args))


if __name__ == "__main__":
    raise SystemExit(main())
